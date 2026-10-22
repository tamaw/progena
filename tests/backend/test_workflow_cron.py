import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.backend.events.documents import seed_document_types
from src.backend.storage.documents import BACKEND_STATE_PREFIX, InMemoryDocumentStore
from src.backend.workflows.cron import WorkflowCronClock, workflow_cron_state_key
from src.backend.workflows.service import (
    PromptExecutionResult,
    WorkflowExecutor,
    get_workflow_run,
    list_workflow_definitions,
    list_workflow_runs,
    save_workflow_definition,
)
from src.progenitor.workflows.documents import default_workflow_definition
from src.progenitor.workflows.events import build_cron_trigger_output
from src.progenitor.workflows.models import WorkflowNodeCron

START = datetime(2026, 10, 12, 8, 30, tzinfo=UTC)


def cron_workflow(*, expression="0 9 * * 1-5", timezone="UTC", overlap="skip"):
    """Cron trigger feeding ``scheduled_for`` into an agent template."""

    payload = default_workflow_definition()
    payload["graph"] = {
        "nodes": {
            "cron": {
                "kind": "cron",
                "title": "Weekdays",
                "expression": expression,
                "timezone": timezone,
                "overlap": overlap,
            },
            "agent": {
                "kind": "agent",
                "title": "Digest",
                "template": "Due at {scheduled_for}",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
            },
        },
        "edges": [
            {
                "from": {"node": "cron", "port": "scheduled_for"},
                "to": {"node": "agent", "port": "scheduled_for"},
            }
        ],
    }
    payload["ui"] = {}
    return payload


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    async def trigger_workflow(
        self, workflow_id, trigger, source="manual", ancestry=None, trigger_node_id=None
    ):
        self.calls.append(
            {
                "workflow_id": workflow_id,
                "trigger": trigger,
                "source": source,
                "node": trigger_node_id,
            }
        )
        return None


class Clock:
    def __init__(self, store, executor):
        self.now = START
        self.clock = WorkflowCronClock(
            store_getter=lambda: store,
            definitions_getter=list_workflow_definitions,
            executor_getter=lambda: executor,
            now=lambda: self.now,
        )

    async def at(self, when):
        self.now = when
        return await self.clock.tick()


def _setup(**cron):
    store = InMemoryDocumentStore()
    seed_document_types(store)
    definition = save_workflow_definition(store, cron_workflow(timezone="UTC", **cron))
    executor = RecordingExecutor()
    return store, definition, executor, Clock(store, executor)


@pytest.mark.asyncio
async def test_new_cron_node_waits_for_its_next_due_time_then_fires_once():
    _store, definition, executor, clock = _setup(expression="0 9 * * *")

    assert await clock.at(START) == START.replace(hour=9, minute=0)
    assert executor.calls == []

    next_due = await clock.at(START.replace(hour=9, minute=0, second=2))
    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["workflow_id"] == definition.id
    assert call["source"] == "cron"
    assert call["node"] == "cron"
    assert call["trigger"]["scheduled_for"] == "2026-10-12T09:00:00+00:00"
    assert call["trigger"]["fired_at"] == "2026-10-12T09:00:02+00:00"
    assert next_due == datetime(2026, 10, 13, 9, 0, tzinfo=UTC)

    await clock.at(START.replace(hour=9, minute=0, second=30))
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_downtime_across_several_due_times_fires_one_catch_up_for_the_latest():
    store, _definition, executor, _clock = _setup(expression="*/5 * * * *")
    first = Clock(store, executor)
    await first.at(START)

    restarted = Clock(store, executor)
    await restarted.at(START + timedelta(minutes=23))

    assert len(executor.calls) == 1
    assert executor.calls[0]["trigger"]["scheduled_for"] == "2026-10-12T08:50:00+00:00"


@pytest.mark.asyncio
async def test_paused_workflow_skips_due_times_and_does_not_replay_them_on_resume():
    store, definition, executor, clock = _setup(expression="*/5 * * * *")
    await clock.at(START)
    paused = definition.model_dump(mode="json", by_alias=True) | {"paused": True}
    save_workflow_definition(store, paused)

    assert await clock.at(START + timedelta(minutes=12)) is None
    save_workflow_definition(store, paused | {"paused": False})
    await clock.at(START + timedelta(minutes=13))

    assert executor.calls == []


@pytest.mark.asyncio
async def test_resume_after_a_tick_inside_the_pause_does_not_fire_paused_due_times():
    store, definition, executor, clock = _setup(expression="*/5 * * * *")
    await clock.at(START)
    paused = definition.model_dump(mode="json", by_alias=True) | {"paused": True}
    save_workflow_definition(store, paused)
    # The pause wakes the clock before any due time passes.
    await clock.at(START + timedelta(minutes=1))

    save_workflow_definition(store, paused | {"paused": False})
    next_due = await clock.at(START + timedelta(minutes=12))

    assert executor.calls == []
    assert next_due == START + timedelta(minutes=15)
    await clock.at(START + timedelta(minutes=15, seconds=1))
    assert [call["trigger"]["scheduled_for"] for call in executor.calls] == [
        "2026-10-12T08:45:00+00:00"
    ]


def test_clock_restarted_under_a_new_event_loop_keeps_running():
    store, _definition, executor, _clock = _setup(expression="*/5 * * * *")
    clock = WorkflowCronClock(
        store_getter=lambda: store,
        definitions_getter=list_workflow_definitions,
        executor_getter=lambda: executor,
    )

    async def lifespan():
        await clock.start()
        await asyncio.sleep(0.01)  # the run loop now waits on its wake event
        clock.wake()
        await asyncio.sleep(0.01)
        task = clock._task
        assert task is not None and not task.done()
        await clock.stop()

    asyncio.run(lifespan())
    asyncio.run(lifespan())


@pytest.mark.asyncio
async def test_changing_the_expression_restarts_counting_from_now():
    store, definition, executor, clock = _setup(expression="0 9 * * *")
    await clock.at(START)
    edited = definition.model_dump(mode="json", by_alias=True)
    edited["graph"]["nodes"]["cron"]["expression"] = "0 8 * * *"
    save_workflow_definition(store, edited)

    # 08:00 today already passed before the edit, so nothing is caught up.
    assert await clock.at(START + timedelta(minutes=1)) == datetime(2026, 10, 13, 8, 0, tzinfo=UTC)
    assert executor.calls == []


@pytest.mark.asyncio
async def test_state_of_deleted_cron_nodes_is_removed():
    store, definition, _executor, clock = _setup()
    await clock.at(START)
    key = workflow_cron_state_key(definition.id, "cron")
    assert store.get_document(BACKEND_STATE_PREFIX, key, None) is not None

    store.delete_document("progenitor.state", f"workflow:{definition.id}")
    await clock.at(START + timedelta(minutes=1))

    assert store.get_document(BACKEND_STATE_PREFIX, key, None) is None


class SlowRunner:
    def __init__(self):
        self.release = asyncio.Event()
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        await self.release.wait()
        return PromptExecutionResult(response="done", thread_id=kwargs["thread_id"])


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := predicate():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


@pytest.mark.asyncio
@pytest.mark.parametrize(("overlap", "expected_runs"), [("skip", 1), ("queue", 2)])
async def test_cron_fire_during_an_active_run_follows_the_node_overlap(overlap, expected_runs):
    store = InMemoryDocumentStore()
    seed_document_types(store)
    definition = save_workflow_definition(store, cron_workflow(timezone="UTC", overlap=overlap))
    runner = SlowRunner()
    executor = WorkflowExecutor(
        store_getter=lambda: store, prompt_runner=runner, emit_event=lambda *_: None
    )
    node = definition.graph.nodes["cron"]
    assert isinstance(node, WorkflowNodeCron)

    def fire(minute):
        due = START.replace(minute=minute)
        return executor.trigger_workflow(
            definition.id,
            build_cron_trigger_output(node, scheduled_for=due, fired_at=due),
            source="cron",
            trigger_node_id="cron",
        )

    first = await fire(0)
    await _wait_for(lambda: runner.calls)
    second = await fire(5)
    assert (second is None) == (overlap == "skip")

    runner.release.set()
    await _wait_for(
        lambda: (
            len(runner.calls) == expected_runs
            and all(
                run.status == "completed"
                for run in list_workflow_runs(store, workflow_id=definition.id)
            )
        )
    )
    assert first is not None
    completed = get_workflow_run(store, first.run_id)
    assert completed is not None and completed.source == "cron"
    assert "Due at 2026-10-12T08:00:00+00:00" in runner.calls[0]["input_text"]


@pytest.mark.asyncio
async def test_pause_and_resume_between_ticks_does_not_replay_paused_due_times():
    store, definition, executor, clock = _setup(expression="*/5 * * * *")
    await clock.at(START)
    paused = definition.model_dump(mode="json", by_alias=True) | {"paused": True}
    # Both land between ticks; the clock only hears of them as document events.
    clock.clock.workflow_changed(
        save_workflow_definition(store, paused).model_dump(mode="json", by_alias=True)
    )
    resumed = save_workflow_definition(store, paused | {"paused": False})
    clock.clock.workflow_changed(resumed.model_dump(mode="json", by_alias=True))

    next_due = await clock.at(START + timedelta(minutes=12))

    assert executor.calls == []
    assert next_due == START + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_backends_sharing_a_store_fire_each_due_time_once():
    store, _definition, executor, first = _setup(expression="*/5 * * * *")
    await first.at(START)
    stale = list(store.list_documents(BACKEND_STATE_PREFIX, "workflow_cron:"))

    class StaleReads:
        """A second backend that read the cron state before the first fired."""

        def __getattr__(self, name):
            return getattr(store, name)

        def list_documents(self, prefix, key_prefix=""):
            if prefix == BACKEND_STATE_PREFIX:
                return stale
            return store.list_documents(prefix, key_prefix)

    second = WorkflowCronClock(
        store_getter=StaleReads,
        definitions_getter=list_workflow_definitions,
        executor_getter=lambda: executor,
        now=lambda: START + timedelta(minutes=5, seconds=1),
    )
    await first.at(START + timedelta(minutes=5, seconds=1))
    await second.tick()

    assert [call["trigger"]["scheduled_for"] for call in executor.calls] == [
        "2026-10-12T08:35:00+00:00"
    ]


def test_state_keys_of_ids_with_colons_do_not_collide():
    assert workflow_cron_state_key("a:b", "c") != workflow_cron_state_key("a", "b:c")
