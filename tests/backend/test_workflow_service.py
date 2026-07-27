import asyncio

import pytest

from app.backend.document_store import InMemoryDocumentStore
from app.backend.workflow_service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    get_workflow_definition,
    get_workflow_queue,
    get_workflow_run,
    list_workflow_runs,
    save_workflow_definition,
)
from app.progenitor.workflows import default_workflow_definition


def _three_prompt_workflow():
    workflow = default_workflow_definition(title="Three prompt workflow")
    event_id, prompt_a_id = list(workflow["graph"]["nodes"])
    prompt_b_id = "prompt_b"
    prompt_c_id = "prompt_c"
    workflow["graph"]["nodes"][prompt_a_id]["prompt"] = "First prompt"
    workflow["graph"]["nodes"][prompt_b_id] = {
        "kind": "prompt",
        "title": "Second prompt",
        "prompt": "Second prompt",
        "agent_name": "progenitor",
        "role": None,
    }
    workflow["graph"]["nodes"][prompt_c_id] = {
        "kind": "prompt",
        "title": "Third prompt",
        "prompt": "Third prompt",
        "agent_name": "progenitor",
        "role": None,
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "default"},
            "to": {"node": prompt_a_id, "port": "default"},
        },
        {
            "from": {"node": prompt_a_id, "port": "default"},
            "to": {"node": prompt_b_id, "port": "default"},
        },
        {
            "from": {"node": prompt_b_id, "port": "default"},
            "to": {"node": prompt_c_id, "port": "default"},
        },
    ]
    workflow["ui"]["nodes"][prompt_b_id] = {
        "position": {"x": 816, "y": 160},
        "size": {
            "data": {"width": 340, "height": 160},
            "settings": {"width": 340, "height": 280},
        },
    }
    workflow["ui"]["nodes"][prompt_c_id] = {
        "position": {"x": 1176, "y": 160},
        "size": {
            "data": {"width": 340, "height": 160},
            "settings": {"width": 340, "height": 280},
        },
    }
    return workflow, event_id, prompt_a_id, prompt_b_id, prompt_c_id


async def _wait_for(predicate, *, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for condition")


@pytest.mark.asyncio
async def test_executor_queues_second_trigger_behind_active_run():
    store = InMemoryDocumentStore()
    definition = save_workflow_definition(store, default_workflow_definition(title="Queue test"))
    events = []
    first_prompt_started = asyncio.Event()
    release_first_prompt = asyncio.Event()
    call_count = 0

    async def prompt_runner(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_prompt_started.set()
            await release_first_prompt.wait()
        return PromptExecutionResult(
            response=f"response {call_count}",
            thread_id=kwargs["thread_id"] or "thread_1",
        )

    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=prompt_runner,
        emit_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    trigger = build_manual_trigger_event(definition)
    first_run = await executor.trigger_workflow(definition.id, trigger)
    assert first_run is not None
    await _wait_for(lambda: first_prompt_started.is_set())

    queued = await executor.trigger_workflow(definition.id, trigger)
    assert queued is not None
    assert len(get_workflow_queue(store, definition.id)) == 1

    release_first_prompt.set()
    await _wait_for(
        lambda: len([run for run in list_workflow_runs(store) if run.status == "completed"]) == 2
    )

    runs = list_workflow_runs(store, workflow_id=definition.id)
    assert [run.status for run in runs] == ["completed", "completed"]
    assert len(get_workflow_queue(store, definition.id)) == 0
    assert [event_type for event_type, _payload in events].count("workflow.run.started") == 2


@pytest.mark.asyncio
async def test_executor_marks_failed_run_and_stops_downstream_nodes():
    store = InMemoryDocumentStore()
    workflow, event_id, prompt_a_id, prompt_b_id, _prompt_c_id = _three_prompt_workflow()
    workflow["graph"]["edges"] = workflow["graph"]["edges"][:2]
    del workflow["graph"]["nodes"]["prompt_c"]
    del workflow["ui"]["nodes"]["prompt_c"]
    definition = save_workflow_definition(store, workflow)

    async def prompt_runner(**kwargs):
        if kwargs["node_id"] == prompt_a_id:
            raise RuntimeError("first prompt exploded")
        return PromptExecutionResult(response="should not run", thread_id="thread_1")

    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=prompt_runner,
        emit_event=lambda *_args, **_kwargs: None,
    )

    trigger = build_manual_trigger_event(definition)
    run = await executor.trigger_workflow(definition.id, trigger)
    assert run is not None

    failed = await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "failed"
            ),
            None,
        )
    )
    assert failed.last_error == "first prompt exploded"
    assert failed.completed_node_ids == [event_id]
    assert failed.current_node_id == event_id
    assert get_workflow_run(store, run.run_id).status == "failed"


@pytest.mark.asyncio
async def test_executor_resume_continues_from_persisted_checkpoint():
    store = InMemoryDocumentStore()
    workflow, event_id, prompt_a_id, prompt_b_id, prompt_c_id = _three_prompt_workflow()
    definition = save_workflow_definition(store, workflow)
    second_prompt_started = asyncio.Event()
    release_second_prompt = asyncio.Event()
    call_order = []

    async def prompt_runner(**kwargs):
        call_order.append(kwargs["node_id"])
        if kwargs["node_id"] == prompt_b_id:
            second_prompt_started.set()
            await release_second_prompt.wait()
        return PromptExecutionResult(
            response=f"output for {kwargs['node_id']}",
            thread_id=kwargs["thread_id"] or "thread_resume",
        )

    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=prompt_runner,
        emit_event=lambda *_args, **_kwargs: None,
    )

    trigger = build_manual_trigger_event(definition)
    run = await executor.trigger_workflow(definition.id, trigger)
    assert run is not None
    await _wait_for(lambda: second_prompt_started.is_set())

    paused = await executor.pause_workflow(definition.id)
    assert paused is not None
    release_second_prompt.set()

    paused_run = await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id
                and item.status == "paused"
                and item.completed_node_ids == [event_id, prompt_a_id, prompt_b_id]
            ),
            None,
        )
    )
    assert paused_run.completed_node_ids == [event_id, prompt_a_id, prompt_b_id]
    assert paused_run.current_node_id == prompt_b_id

    resumed = await executor.resume_workflow(definition.id)
    assert resumed is not None

    completed = await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "completed"
            ),
            None,
        )
    )
    assert completed.completed_node_ids == [event_id, prompt_a_id, prompt_b_id, prompt_c_id]
    assert call_order == [prompt_a_id, prompt_b_id, prompt_c_id]


@pytest.mark.asyncio
async def test_executor_picks_up_definition_updates_from_store_between_nodes():
    store = InMemoryDocumentStore()
    workflow, _event_id, prompt_a_id, prompt_b_id, _prompt_c_id = _three_prompt_workflow()
    workflow["graph"]["edges"] = workflow["graph"]["edges"][:2]
    del workflow["graph"]["nodes"]["prompt_c"]
    del workflow["ui"]["nodes"]["prompt_c"]
    definition = save_workflow_definition(store, workflow)
    first_prompt_started = asyncio.Event()
    release_first_prompt = asyncio.Event()
    seen_prompts = []

    async def prompt_runner(**kwargs):
        seen_prompts.append(kwargs["node"].prompt)
        if kwargs["node_id"] == prompt_a_id:
            first_prompt_started.set()
            await release_first_prompt.wait()
        return PromptExecutionResult(response="ok", thread_id=kwargs["thread_id"] or "thread_live")

    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=prompt_runner,
        emit_event=lambda *_args, **_kwargs: None,
    )

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    assert run is not None
    await _wait_for(lambda: first_prompt_started.is_set())

    current = get_workflow_definition(store, definition.id)
    updated = current.model_dump(mode="json", by_alias=True)
    updated["graph"]["nodes"][prompt_b_id]["prompt"] = "Updated from virtual file"
    save_workflow_definition(store, updated)
    release_first_prompt.set()

    await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "completed"
            ),
            None,
        )
    )
    assert seen_prompts == ["First prompt", "Updated from virtual file"]


def _moved_trigger(from_column, to_column):
    return {
        "event_type": "kanban.card.moved",
        "timestamp": "2026-07-08T00:00:00+00:00",
        "payload": {
            "event_type": "kanban.card.moved",
            "from": {"column_id": from_column, "position": 0},
            "to": {"column_id": to_column, "position": 0},
        },
    }


@pytest.mark.asyncio
async def test_dispatch_event_filters_by_from_and_to_column():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition(
        title="Column filter test", event_type="kanban.card.moved"
    )
    workflow["trigger"]["from_column"] = "backlog"
    workflow["trigger"]["to_column"] = "plan"
    definition = save_workflow_definition(store, workflow)

    async def prompt_runner(**kwargs):
        return PromptExecutionResult(response="ok", thread_id=kwargs["thread_id"] or "thread_1")

    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=prompt_runner,
        emit_event=lambda *_args, **_kwargs: None,
    )

    non_matching = await executor.dispatch_event(
        "kanban.card.moved", _moved_trigger("plan", "build")
    )
    assert non_matching == []

    matching = await executor.dispatch_event("kanban.card.moved", _moved_trigger("backlog", "plan"))
    assert len(matching) == 1
    assert matching[0].workflow_id == definition.id

    await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == matching[0].run_id and item.status == "completed"
            ),
            None,
        )
    )


@pytest.mark.asyncio
async def test_executor_routes_condition_and_sends_inbox_message():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition(title="Condition inbox")
    event_id, _prompt_id = list(workflow["graph"]["nodes"])
    condition_id = "condition_1"
    inbox_id = "inbox_1"
    workflow["graph"]["nodes"].pop(_prompt_id)
    workflow["graph"]["nodes"][condition_id] = {
        "kind": "condition",
        "title": "Done?",
        "expression": 'payload.card.status == "done"',
    }
    workflow["graph"]["nodes"][inbox_id] = {
        "kind": "inbox",
        "title": "Inbox",
        "message_title": "Completed",
        "message": "The card is done.",
        "level": "success",
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "default"},
            "to": {"node": condition_id, "port": "default"},
        },
        {
            "from": {"node": condition_id, "port": "true"},
            "to": {"node": inbox_id, "port": "default"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=lambda **_kwargs: None,
        emit_event=lambda *_args, **_kwargs: None,
    )

    trigger = build_manual_trigger_event(
        definition,
        payload={"card": {"status": "done"}},
    )
    run = await executor.trigger_workflow(definition.id, trigger)
    assert run is not None
    await _wait_for(lambda: any(item.status == "completed" for item in list_workflow_runs(store)))
    inbox = store.list_documents("progenitor.state", "inbox_message:")
    assert len(inbox) == 1
    assert inbox[0][1].payload["title"] == "Completed"


@pytest.mark.asyncio
async def test_executor_ends_when_condition_port_is_unmatched():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition(title="Unmatched condition")
    event_id, prompt_id = list(workflow["graph"]["nodes"])
    condition_id = "condition_1"
    inbox_id = "inbox_1"
    workflow["graph"]["nodes"].pop(prompt_id)
    workflow["graph"]["nodes"][condition_id] = {
        "kind": "condition",
        "title": "Done?",
        "expression": 'payload.card.status == "done"',
    }
    workflow["graph"]["nodes"][inbox_id] = {
        "kind": "inbox",
        "title": "Inbox",
        "message_title": "Completed",
        "message": "The card is done.",
        "level": "success",
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "default"},
            "to": {"node": condition_id, "port": "default"},
        },
        {
            "from": {"node": condition_id, "port": "true"},
            "to": {"node": inbox_id, "port": "default"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=lambda **_kwargs: None,
        emit_event=lambda *_args, **_kwargs: None,
    )

    run = await executor.trigger_workflow(
        definition.id,
        build_manual_trigger_event(definition, payload={"card": {"status": "not_done"}}),
    )
    assert run is not None
    completed = await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "completed"
            ),
            None,
        )
    )
    assert completed.last_output == "false"
    assert store.list_documents("progenitor.state", "inbox_message:") == []


@pytest.mark.asyncio
async def test_executor_pauses_and_resolves_approval():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition(title="Approval")
    event_id, prompt_id = list(workflow["graph"]["nodes"])
    approval_id = "approval_1"
    workflow["graph"]["nodes"].pop(prompt_id)
    workflow["graph"]["nodes"][approval_id] = {
        "kind": "approval",
        "title": "Approve",
        "message": "Proceed?",
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "default"},
            "to": {"node": approval_id, "port": "default"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    executor = WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=lambda **_kwargs: None,
        emit_event=lambda *_args, **_kwargs: None,
    )

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    assert run is not None
    paused = await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "paused"
            ),
            None,
        )
    )
    assert paused.waiting_node_id == approval_id
    resolved = await executor.resolve_approval(definition.id, run.run_id, approved=True)
    assert resolved is not None
    await _wait_for(
        lambda: next(
            (
                item
                for item in list_workflow_runs(store)
                if item.run_id == run.run_id and item.status == "completed"
            ),
            None,
        )
    )
