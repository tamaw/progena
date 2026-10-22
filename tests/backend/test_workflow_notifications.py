"""Alert documents, pause alerts, and approval behavior."""

import asyncio

import pytest

from app.backend.alerts import service as alert_store
from app.backend.events.documents import seed_document_types
from app.backend.storage.documents import (
    EVENT_LOG_START,
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.workflows.service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    get_workflow_run,
    save_workflow_definition,
)
from app.progenitor.workflows import default_workflow_definition


@pytest.fixture
def store():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    set_default_document_store(store)
    yield store
    set_default_document_store(InMemoryDocumentStore())


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := predicate():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def _executor(store):
    async def runner(**kwargs):
        return PromptExecutionResult(response="ok", thread_id=kwargs["thread_id"])

    return WorkflowExecutor(
        store_getter=lambda: store, prompt_runner=runner, emit_event=lambda *_a: None
    )


def _workflow(nodes, edges, **fields):
    workflow = default_workflow_definition()
    event_id = next(i for i, n in workflow["graph"]["nodes"].items() if n["kind"] == "event")
    workflow["graph"] = {
        "nodes": {event_id: workflow["graph"]["nodes"][event_id], **nodes},
        "edges": [
            {"from": {"node": event_id, "port": port}, "to": {"node": node, "port": to}}
            for node, port, to in edges
        ],
    }
    return {**workflow, **fields}


def test_alerts_are_durable_documents_and_prune_oldest(store, monkeypatch):
    monkeypatch.setattr(alert_store, "MAX_ALERTS", 2)
    first = alert_store.create_alert(type="info", title="One", message="1")
    alert_store.create_alert(type="warning", title="Two", message="2")
    third = alert_store.create_alert(type="error", title="Three", message="3")

    assert [a.title for a in alert_store.list_alerts()] == ["Three", "Two"]
    assert alert_store.get_alert(first.id) is None
    assert store.get_document(PROGENITOR_STATE_PREFIX, f"alert:{third.id}").payload["urgent"]

    assert alert_store.mark_alerts_viewed([third.id]) == 1
    assert alert_store.get_alert(third.id).viewed
    assert alert_store.mark_alerts_viewed([first.id]) == 0
    created = [e.key for e in store.read_document_events(EVENT_LOG_START, 50) if e.op == "created"]
    assert f"alert:{third.id}" in created


def _approval_workflow(store, *, notify_on_pause, approver="any"):
    return save_workflow_definition(
        store,
        _workflow(
            {
                "gate": {
                    "kind": "approval",
                    "title": "Gate",
                    "message": "Ship it?",
                    "approver": approver,
                    "input_schema": {
                        "type": "object",
                        "required": ["key"],
                        "properties": {"key": {"type": "string"}},
                    },
                }
            },
            [("gate", "key", "key")],
            notify_on_pause=notify_on_pause,
        ),
    )


async def _paused(store, executor, definition):
    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    return await _wait_for(
        lambda: (r := get_workflow_run(store, run.run_id)).status == "paused" and r
    )


@pytest.mark.asyncio
async def test_pause_alert_only_when_the_workflow_asks_for_it(store):
    executor = _executor(store)
    await _paused(store, executor, _approval_workflow(store, notify_on_pause=False))
    assert alert_store.list_alerts() == []

    paused = await _paused(
        store, executor, _approval_workflow(store, notify_on_pause=True, approver="human")
    )
    [alert] = alert_store.list_alerts()
    assert alert.requires_response and alert.message == "Ship it?"
    assert alert.context == {
        "kind": "workflow_approval",
        "workflow_id": paused.workflow_id,
        "run_id": paused.run_id,
        "node_id": "gate",
        "approver": "human",
        "answers": ["approve", "reject"],
    }


@pytest.mark.asyncio
async def test_progenitor_cannot_decide_a_human_only_approval(store):
    executor = _executor(store)
    paused = await _paused(
        store, executor, _approval_workflow(store, notify_on_pause=False, approver="human")
    )

    with pytest.raises(PermissionError, match="person"):
        await executor.resolve_approval(
            paused.workflow_id, paused.run_id, approved=True, answered_by="agent"
        )
    assert get_workflow_run(store, paused.run_id).status == "paused"

    resolved = await executor.resolve_approval(paused.workflow_id, paused.run_id, approved=True)
    assert resolved is not None and resolved.status != "paused"


@pytest.mark.asyncio
async def test_progenitor_may_decide_an_any_approver_approval(store):
    executor = _executor(store)
    paused = await _paused(store, executor, _approval_workflow(store, notify_on_pause=False))

    resolved = await executor.resolve_approval(
        paused.workflow_id, paused.run_id, approved=False, answered_by="agent"
    )
    assert resolved is not None
    assert resolved.pending_approval["approved"] is False


@pytest.mark.asyncio
async def test_routine_human_pause_raises_an_alert_with_its_question(store):
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "routine:greet",
        {"id": "greet", "name": "Greet", "version": 1, "entry": "n1", "nodes": [], "edges": []},
    )
    definition = save_workflow_definition(
        store,
        _workflow(
            {"run": {"kind": "routine", "title": "Ask", "routine": "greet"}},
            [("run", "key", "message")],
            notify_on_pause=True,
        ),
    )
    executor = _executor(store)
    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    waiting = await _wait_for(
        lambda: (r := get_workflow_run(store, run.run_id)).pending_routine and r
    )

    await executor.handle_routine_run_event(
        {
            "session_id": waiting.pending_routine["session_id"],
            "status": "paused",
            "thread_id": "routine:greet:unpinned:s",
            "question": "Proceed?",
            "allowed_responses": ["yes", "no"],
            "approver": "human",
        }
    )

    [alert] = alert_store.list_alerts()
    assert alert.message == "Proceed?"
    assert alert.context["kind"] == "routine_human"
    assert alert.context["thread_id"] == "routine:greet:unpinned:s"
    assert alert.context["approver"] == "human"
    assert alert.context["answers"] == ["yes", "no"]
