"""A workflow routine node runs a routine deterministically and waits on its lifecycle."""

import asyncio

import pytest

from app.backend.document_events import seed_document_types
from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.workflow_service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    get_workflow_run,
    save_workflow_definition,
)
from app.progenitor.workflows import default_workflow_definition

TYPED_ROUTINE = {
    "id": "summarize",
    "name": "Summarize",
    "version": 1,
    "entry": "n1",
    "nodes": [],
    "edges": [],
    "input_schema": {
        "type": "object",
        "required": ["card"],
        "properties": {"card": {"type": "string"}},
    },
    "output_schema": {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    },
}
PLAIN_ROUTINE = {
    "id": "greet",
    "name": "Greet",
    "version": 1,
    "entry": "n1",
    "nodes": [],
    "edges": [],
}


class RecordingRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return PromptExecutionResult(response="noted", thread_id=kwargs["thread_id"])


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := predicate():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def _setup(routine, *, port, consumer_template=None):
    store = InMemoryDocumentStore()
    seed_document_types(store)
    store.save_document(PROGENITOR_STATE_PREFIX, f"routine:{routine['id']}", routine)
    workflow = default_workflow_definition()
    event_id = next(i for i, n in workflow["graph"]["nodes"].items() if n["kind"] == "event")
    nodes = {event_id: workflow["graph"]["nodes"][event_id]}
    nodes["run"] = {"kind": "routine", "title": "Run routine", "routine": routine["id"]}
    edges = [{"from": {"node": event_id, "port": "key"}, "to": {"node": "run", "port": port}}]
    if consumer_template:
        nodes["report"] = {"kind": "agent", "title": "Report", "template": consumer_template}
        edges.append(
            {
                "from": {"node": "run", "port": "summary"},
                "to": {"node": "report", "port": "summary"},
            }
        )
    workflow["graph"] = {"nodes": nodes, "edges": edges}
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner()
    executor = WorkflowExecutor(
        store_getter=lambda: store, prompt_runner=runner, emit_event=lambda *_a: None
    )
    return store, definition, executor, runner


def _queued_tasks(store):
    document = store.get_document(PROGENITOR_STATE_PREFIX, "background_tasks", [])
    return list(document.payload) if document is not None else []


async def _start(store, definition, executor):
    run = await executor.trigger_workflow(
        definition.id, build_manual_trigger_event(definition, payload={"key": "kanban_card:a"})
    )
    waiting = await _wait_for(
        lambda: (r := get_workflow_run(store, run.run_id)) and r.pending_routine and r
    )
    return waiting


def test_routine_ports_come_from_its_schemas_else_message():
    _store, typed, _e, _r = _setup(TYPED_ROUTINE, port="card")
    node = typed.graph.nodes["run"]
    assert set(node.input_schema["properties"]) == {"card"}
    assert set(node.output_schema["properties"]) == {"summary", "thread_id"}

    _store, plain, _e, _r = _setup(PLAIN_ROUTINE, port="message")
    node = plain.graph.nodes["run"]
    assert set(node.input_schema["properties"]) == {"message"}
    assert set(node.output_schema["properties"]) == {"message", "thread_id"}


def test_unknown_routine_is_rejected():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    workflow["graph"]["nodes"]["run"] = {"kind": "routine", "title": "R", "routine": "nope"}
    with pytest.raises(ValueError, match="unknown routine 'nope'"):
        save_workflow_definition(store, workflow)


def test_thread_id_output_field_is_reserved():
    routine = {
        **TYPED_ROUTINE,
        "output_schema": {"type": "object", "properties": {"thread_id": {"type": "string"}}},
    }
    with pytest.raises(ValueError, match="reserved"):
        _setup(routine, port="card")


@pytest.mark.asyncio
async def test_typed_routine_output_feeds_the_next_node():
    store, definition, executor, runner = _setup(
        TYPED_ROUTINE, port="card", consumer_template="Report {summary}"
    )
    waiting = await _start(store, definition, executor)
    pending = waiting.pending_routine

    [task] = _queued_tasks(store)
    assert task["metadata"]["kind"] == "routine_run"
    assert task["metadata"]["parameters"] == {"card": "kanban_card:a"}
    assert task["metadata"]["session_id"] == pending["session_id"]
    assert runner.calls == []

    await executor.handle_routine_run_event(
        {
            "session_id": pending["session_id"],
            "status": "completed",
            "thread_id": "routine:summarize:unpinned:x",
            "output": {"summary": "one card"},
        }
    )
    done = await _wait_for(
        lambda: (r := get_workflow_run(store, waiting.run_id)).status == "completed" and r
    )
    assert done.node_outputs["run"] == {
        "summary": "one card",
        "thread_id": "routine:summarize:unpinned:x",
    }
    assert "Report one card" in runner.calls[0]["input_text"]
    assert done.pending_routine is None


@pytest.mark.asyncio
async def test_plain_routine_gets_a_message_and_returns_one():
    store, definition, executor, _runner = _setup(PLAIN_ROUTINE, port="message")
    waiting = await _start(store, definition, executor)
    [task] = _queued_tasks(store)
    assert task["metadata"]["message"] == "kanban_card:a"
    assert "parameters" not in task["metadata"]

    await executor.handle_routine_run_event(
        {
            "session_id": waiting.pending_routine["session_id"],
            "status": "completed",
            "thread_id": "t",
            "output": {"message": "hello"},
        }
    )
    done = await _wait_for(
        lambda: (r := get_workflow_run(store, waiting.run_id)).status == "completed" and r
    )
    assert done.node_outputs["run"] == {"message": "hello", "thread_id": "t"}


@pytest.mark.asyncio
async def test_human_pause_pauses_the_run_until_the_routine_continues():
    store, definition, executor, _runner = _setup(PLAIN_ROUTINE, port="message")
    waiting = await _start(store, definition, executor)
    session_id = waiting.pending_routine["session_id"]

    await executor.handle_routine_run_event(
        {"session_id": session_id, "status": "paused", "thread_id": "routine:greet:u:s"}
    )
    paused = get_workflow_run(store, waiting.run_id)
    assert paused.status == "paused"
    assert paused.pending_routine["status"] == "paused"
    assert paused.pending_routine["thread_id"] == "routine:greet:u:s"

    # Resuming the workflow alone cannot skip the unanswered human step.
    assert (await executor.resume_workflow(definition.id)).status == "paused"
    assert await executor.resolve_approval(definition.id, waiting.run_id, approved=True) is None

    await executor.handle_routine_run_event({"session_id": session_id, "status": "started"})
    assert get_workflow_run(store, waiting.run_id).status == "running"
    await executor.handle_routine_run_event(
        {"session_id": session_id, "status": "completed", "output": {"message": "answered"}}
    )
    done = await _wait_for(
        lambda: (r := get_workflow_run(store, waiting.run_id)).status == "completed" and r
    )
    assert done.node_outputs["run"]["message"] == "answered"
    assert len(_queued_tasks(store)) == 1


@pytest.mark.asyncio
async def test_routine_failure_or_bad_output_fails_the_run():
    store, definition, executor, _runner = _setup(PLAIN_ROUTINE, port="message")
    waiting = await _start(store, definition, executor)
    await executor.handle_routine_run_event(
        {"session_id": waiting.pending_routine["session_id"], "status": "failed", "error": "boom"}
    )
    failed = get_workflow_run(store, waiting.run_id)
    assert failed.status == "failed"
    assert failed.last_error == "routine 'greet' failed: boom"

    store, definition, executor, _runner = _setup(TYPED_ROUTINE, port="card")
    waiting = await _start(store, definition, executor)
    await executor.handle_routine_run_event(
        {
            "session_id": waiting.pending_routine["session_id"],
            "status": "completed",
            "output": {},
        }
    )
    failed = get_workflow_run(store, waiting.run_id)
    assert failed.status == "failed"
    assert "summary" in failed.last_error


@pytest.mark.asyncio
async def test_events_for_other_sessions_are_ignored():
    store, definition, executor, _runner = _setup(PLAIN_ROUTINE, port="message")
    waiting = await _start(store, definition, executor)
    other = f"{waiting.run_id}_deadbeef"
    await executor.handle_routine_run_event({"session_id": other, "status": "failed"})
    await executor.handle_routine_run_event({"session_id": "ui-session", "status": "failed"})
    assert get_workflow_run(store, waiting.run_id).status == "running"
