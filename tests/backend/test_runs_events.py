"""Consumer-visible trace subscription races and cache freshness."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from src.backend.events.documents import TypedDocumentEvent
from src.backend.runs import events
from src.backend.runs.models import RunTrace
from src.backend.storage.documents import (
    EVENT_LOG_START,
    PROGENA_STATE_PREFIX,
    DocumentEvent,
    InMemoryDocumentStore,
)
from src.backend.workflows import events as workflow_events
from src.progena.routines.events import RUN_EVENT_PREFIX


def _trace(run_id, *, kind="workflow", status="running", tokens=None):
    return RunTrace(
        kind=kind,
        run_id=run_id,
        name=run_id,
        status=status,
        started_at="2026-10-28T12:00:00Z",
        tokens=tokens,
    )


def _routine_event(payload, *, seq=1):
    return TypedDocumentEvent(
        event=DocumentEvent(
            seq=seq,
            txid=seq,
            prefix=PROGENA_STATE_PREFIX,
            key=f"{RUN_EVENT_PREFIX}{payload['request_id']}:{payload['status']}",
            op="created",
            before=None,
            after=payload,
            origin=None,
            created_at=datetime(2026, 10, 29, tzinfo=UTC),
        ),
        document_type=None,
        output=None,
    )


@pytest.mark.asyncio
async def test_disconnect_keeps_other_viewers_checkpoint_interest(monkeypatch):
    manager = events.RunsEvents()

    async def build(_store, _kind, run_id):
        return _trace(run_id), {"shared-thread"}

    monkeypatch.setattr(events, "build_trace", build)
    try:
        await manager.subscribe(object(), "first", "workflow", "shared")
        await manager.subscribe(object(), "second", "workflow", "shared")
        manager.disconnected("first")
        assert manager.by_thread["shared-thread"] == {("workflow", "shared")}
        assert manager.interests["second"].run_id == "shared"
        manager.disconnected("second")
        assert "shared-thread" not in manager.by_thread
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_slow_previous_selection_cannot_replace_new_selection(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def build(_store, _kind, run_id):
        if run_id == "old":
            entered.set()
            await release.wait()
        return _trace(run_id), {f"thread-{run_id}"}

    monkeypatch.setattr(events, "build_trace", build)
    old = asyncio.create_task(manager.subscribe(object(), "viewer", "workflow", "old"))
    try:
        await entered.wait()
        newest = await manager.subscribe(object(), "viewer", "workflow", "new")
        release.set()
        assert await old is None
        assert newest is not None and newest[0].run_id == "new"
        assert manager.interests["viewer"].run_id == "new"
        assert "thread-old" not in manager.by_thread
        assert manager.by_thread["thread-new"] == {("workflow", "new")}
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_checkpoint_during_initial_hydration_rebuilds_before_subscription_returns(
    monkeypatch,
):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    builds = 0

    async def build(_store, _kind, run_id):
        nonlocal builds
        builds += 1
        if builds == 1:
            entered.set()
            await release.wait()
        return _trace(run_id, tokens=7 if builds == 1 else 17), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    subscribing = asyncio.create_task(manager.subscribe(object(), "viewer", "workflow", "run"))
    try:
        await entered.wait()
        manager.checkpoint_changed(json.dumps({"thread_id": "thread"}))
        release.set()

        selected = await subscribing

        assert selected is not None
        assert selected[0].tokens == 17
        assert builds == 2
        assert manager.by_thread["thread"] == {("workflow", "run")}
        assert not manager.pending
    finally:
        release.set()
        await asyncio.gather(subscribing, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_change_during_build_discards_stale_result_and_caches_current(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    usage = 12

    async def build(_store, _kind, run_id):
        captured_usage = usage
        if captured_usage == 12:
            entered.set()
            await release.wait()
        return _trace(run_id, status="completed", tokens=captured_usage), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    pending = asyncio.create_task(manager.read_trace(object(), "workflow", "run"))
    try:
        await entered.wait()
        usage = 37
        manager._invalidate(("workflow", "run"))
        release.set()
        trace, _ = await pending
        assert trace.tokens == 37
        cached, _ = await manager.read_trace(object(), "workflow", "run")
        assert cached.tokens == 37
        assert manager.completed[("workflow", "run")][0].tokens == 37
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_cancelled_reader_does_not_retain_completed_background_build(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def build(_store, _kind, run_id):
        entered.set()
        await release.wait()
        return _trace(run_id), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    reader = asyncio.create_task(manager.read_trace(object(), "workflow", "run"))
    try:
        await entered.wait()
        shared = manager.builds[("workflow", "run")]
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        release.set()
        await shared
        await asyncio.sleep(0)
        assert not manager.builds
        assert not manager.trace_threads
        assert not manager.by_thread
    finally:
        release.set()
        await asyncio.gather(reader, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_unwatched_trace_get_rebuilds_after_checkpoint_during_hydration(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    builds = 0

    async def build(_store, _kind, run_id):
        nonlocal builds
        builds += 1
        tokens = 7 if builds == 1 else 17
        if builds == 1:
            entered.set()
            await release.wait()
        return _trace(run_id, status="completed", tokens=tokens), {"checkpoint-thread"}

    monkeypatch.setattr(events, "build_trace", build)
    reading = asyncio.create_task(manager.read_trace(object(), "workflow", "run"))
    try:
        await entered.wait()
        assert not manager.interests
        manager.checkpoint_changed(json.dumps({"thread_id": "checkpoint-thread"}))
        release.set()

        trace, threads = await reading

        assert trace.tokens == 17
        assert threads == {"checkpoint-thread"}
        assert builds == 2
        assert manager.completed[("workflow", "run")][0].tokens == 17
    finally:
        release.set()
        await asyncio.gather(reading, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_child_lifecycle_rebuilds_unindexed_parent_trace_dependencies(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    builds = 0
    parent_thread = "routine:parent:revision:run"
    child_thread = "routine:child:revision:run"

    async def build(_store, _kind, run_id):
        nonlocal builds
        builds += 1
        if builds == 1:
            entered.set()
            await release.wait()
            return _trace(run_id, kind="routine", status="completed"), {parent_thread}
        return (
            _trace(run_id, kind="routine", status="completed", tokens=17),
            {parent_thread, child_thread},
        )

    monkeypatch.setattr(events, "build_trace", build)
    reading = asyncio.create_task(manager.read_trace(object(), "routine", "parent"))
    try:
        await entered.wait()
        await manager.document_event(
            _routine_event(
                {
                    "request_id": "child",
                    "status": "started",
                    "thread_id": child_thread,
                    "parent": {"thread_id": parent_thread, "node_id": "child-node"},
                }
            )
        )
        release.set()

        trace, threads = await reading

        assert trace.tokens == 17
        assert threads == {parent_thread, child_thread}
        assert builds == 2
    finally:
        release.set()
        await asyncio.gather(reading, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_checkpoint_row_drops_stale_hydration_after_durable_lifecycle(monkeypatch):
    from src.backend.runs import service

    store = InMemoryDocumentStore()
    manager = events.RunsEvents()
    manager.store = store
    thread_id = "routine:daily:revision:attempt"
    started_at = "2026-10-29T12:00:00+00:00"
    completed_at = "2026-10-29T12:01:00+00:00"
    started = {
        "request_id": "attempt",
        "status": "started",
        "thread_id": thread_id,
        "agent_id": "daily",
        "revision_id": "revision",
        "timestamp": started_at,
    }
    completed = {**started, "status": "completed", "timestamp": completed_at}
    store.upsert_routine_revision(
        "daily",
        "revision",
        {"name": "Daily", "nodes": [{"id": "work", "kind": "prompt", "label": "Work"}]},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        f"{RUN_EVENT_PREFIX}attempt:started",
        started,
    )
    await workflow_events._lifecycle_index.ensure_loaded(store)

    entered = asyncio.Event()
    release = asyncio.Event()
    hydrations = 0
    hydrate = service._hydrate_run_overviews

    async def delay_first_hydration(current_store, rows):
        nonlocal hydrations
        hydrations += 1
        if hydrations == 1:
            entered.set()
            await release.wait()
        await hydrate(current_store, rows)

    published = []

    def record(event_type, payload, **_kwargs):
        if event_type == "run.updated":
            published.append(payload["run"])

    monkeypatch.setattr(service, "_hydrate_run_overviews", delay_first_hydration)
    monkeypatch.setattr(events.stream, "broadcast_ui_event", record)
    hydrating = asyncio.create_task(manager._checkpoint_row(thread_id))
    try:
        await entered.wait()
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}attempt:completed",
            completed,
        )
        workflow_events.routine_events.publish_lifecycle(completed)
        completed_event = next(
            event
            for event in store.read_document_events(EVENT_LOG_START, 10)
            if event.key == f"{RUN_EVENT_PREFIX}attempt:completed"
        )
        await manager.document_event(
            TypedDocumentEvent(event=completed_event, document_type=None, output=None)
        )
        release.set()
        await hydrating

        assert [row["status"] for row in published] == ["completed"]
        assert published[0]["completed_at"] is not None
    finally:
        release.set()
        await asyncio.gather(hydrating, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_native_child_lifecycle_rebuilds_unindexed_parent_trace(monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import timedelta

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from src.backend.runs import service

    class CheckpointState(TypedDict):
        run_meta: dict[str, list[dict[str, object]]]

    async def write_checkpoint(thread_id, run_meta):
        builder = StateGraph(CheckpointState)
        builder.add_node("record", lambda _state: {"run_meta": run_meta})
        builder.add_edge(START, "record")
        builder.add_edge("record", END)
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({}, {"configurable": {"thread_id": thread_id}})

    store = InMemoryDocumentStore()
    manager = events.RunsEvents()
    manager.store = store
    parent_thread = "routine:parent:revision:attempt"
    child_thread = "routine:child:revision:child-attempt"
    started_at = datetime(2026, 10, 29, tzinfo=UTC)
    child_at = (started_at + timedelta(seconds=10)).isoformat()
    parent = {
        "request_id": "attempt",
        "status": "started",
        "thread_id": parent_thread,
        "agent_id": "parent",
        "revision_id": "revision",
        "timestamp": started_at.isoformat(),
    }
    child = {
        "request_id": "child-attempt",
        "status": "started",
        "thread_id": child_thread,
        "agent_id": "child",
        "revision_id": "revision",
        "timestamp": child_at,
        "parent": {"thread_id": parent_thread, "node_id": "call"},
    }
    store.upsert_routine_revision(
        "parent",
        "revision",
        {"name": "Parent", "nodes": [{"id": "call", "kind": "routine", "label": "Call"}]},
    )
    store.upsert_routine_revision(
        "child",
        "revision",
        {"name": "Child", "nodes": [{"id": "work", "kind": "prompt", "label": "Work"}]},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        f"{RUN_EVENT_PREFIX}attempt:started",
        parent,
    )
    await workflow_events._lifecycle_index.ensure_loaded(store)

    saver = InMemorySaver()
    await write_checkpoint(
        parent_thread,
        {
            "call": [
                {
                    "started_at": started_at.isoformat(),
                    "duration_ms": 60_000,
                    "routine": {"request_id": "child-attempt", "thread_id": child_thread},
                }
            ]
        },
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original_saver = service._trace_saver
    original_build = events.build_trace
    built = False

    @asynccontextmanager
    async def native_saver(_store):
        yield saver

    async def hold_first_build(current_store, kind, run_id):
        nonlocal built
        result = await original_build(current_store, kind, run_id)
        if not built:
            built = True
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(service, "_trace_saver", native_saver)
    monkeypatch.setattr(events, "build_trace", hold_first_build)
    reading = asyncio.create_task(manager.read_trace(store, "routine", "attempt"))
    try:
        await entered.wait()
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}child-attempt:started",
            child,
        )
        workflow_events.routine_events.publish_lifecycle(child)
        await write_checkpoint(
            child_thread,
            {"work": [{"started_at": child_at, "duration_ms": 0, "tokens": {"total_tokens": 17}}]},
        )
        child_event = next(
            event
            for event in store.read_document_events(EVENT_LOG_START, 10)
            if event.key == f"{RUN_EVENT_PREFIX}child-attempt:started"
        )
        await manager.document_event(
            TypedDocumentEvent(event=child_event, document_type=None, output=None)
        )
        release.set()

        trace, threads = await reading

        assert trace.tokens == 17
        assert threads == {parent_thread, child_thread}
        assert any(span.tokens == 17 for span in trace.spans)
    finally:
        release.set()
        await asyncio.gather(reading, return_exceptions=True)
        service._trace_saver = original_saver
        events.build_trace = original_build
        await manager.stop()
