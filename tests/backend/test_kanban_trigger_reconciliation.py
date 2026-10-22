"""Kanban workflow triggers fire via the generic document event dispatcher.

A card write (direct store write simulating the agent, or REST route) appends a
DocumentEvent to the log.  The DocumentEventDispatcher drains the log and
starts matching workflows.  These tests verify the end-to-end path without
peeking at deleted cache/reconciler internals.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.backend.events.dispatch as events_dispatch
import app.backend.events.stream as events_stream
import app.backend.lifecycle.state as lifecycle_state
import app.backend.workflows.runtime as workflows_runtime
from app.backend.chat.models import AgentResponse
from app.backend.events.documents import seed_document_types
from app.backend.main import app
from app.backend.monitoring.state import StateReader
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.workflows.service import list_workflow_runs, save_workflow_definition
from app.progenitor.workflows.documents import default_workflow_definition


@pytest.fixture
def workflow_env(tmp_path, monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    monkeypatch.setattr(
        "app.backend.storage.documents.configure_default_document_store", lambda *_a, **_k: store
    )

    state_reader = StateReader(document_store=store)
    monkeypatch.setattr("app.backend.monitoring.state.get_state_reader", lambda: state_reader)

    workflows_runtime._workflow_executor = None
    events_dispatch._document_event_dispatcher = None
    events_stream._ui_event_replay.clear()
    yield store, state_reader
    workflows_runtime._workflow_executor = None
    events_dispatch._document_event_dispatcher = None
    events_stream._ui_event_replay.clear()
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def backend_client(monkeypatch):

    mock_client = AsyncMock()
    mock_client.send_message.return_value = AgentResponse(
        response="{}",
        reasoning=None,
        reasoning_tokens=None,
    )
    monkeypatch.setattr(
        "app.backend.integrations.runtime.get_sse_client", lambda *_a, **_k: mock_client
    )
    lifecycle_state.client = mock_client
    yield mock_client


def _matching_workflow_definition(workflow_id: str, title: str) -> dict:
    """Return a workflow that fires when a kanban card is moved to the 'done' column."""
    workflow = default_workflow_definition(workflow_id=workflow_id, title=title)
    event_id = next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == "event"
    )
    workflow["graph"]["nodes"][event_id].update(
        operations=["updated"],
        changed_fields=["column_id"],
        match={"after.column_id": "done"},
    )
    return workflow


async def _wait_for_completed_run(
    store: InMemoryDocumentStore, workflow_id: str, *, timeout: float = 2.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = list_workflow_runs(store, workflow_id=workflow_id)
        matches = [run.model_dump(mode="json") for run in runs]
        if matches and matches[0]["status"] in {"completed", "failed"}:
            return matches[0]
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for workflow run completion for {workflow_id}")


@pytest.mark.asyncio
async def test_agent_card_move_fires_matching_workflow(workflow_env, backend_client):
    store, _state_reader = workflow_env
    workflow_id = "workflow_agent_move"
    save_workflow_definition(
        store, _matching_workflow_definition(workflow_id, "Agent move workflow")
    )

    # Direct store write simulating the runtime agent's filesystem tool.
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:card_agent_1",
        {
            "id": "card_agent_1",
            "title": "Agent-authored card",
            "summary": "Do the thing",
            "column_id": "backlog",
            "status": "idle",
            "owner": "user",
        },
    )
    await events_dispatch.get_document_event_dispatcher().drain()
    # Non-matching creation should not start the workflow.
    assert list_workflow_runs(store, workflow_id=workflow_id) == []

    # Move the card to done — this must match the workflow event node.
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:card_agent_1",
        {
            "id": "card_agent_1",
            "title": "Agent-authored card",
            "summary": "Do the thing",
            "column_id": "done",
            "status": "idle",
            "owner": "user",
        },
    )
    await events_dispatch.get_document_event_dispatcher().drain()

    run = await _wait_for_completed_run(store, workflow_id)
    assert run["status"] == "completed", run["last_error"]
    event = run["trigger_event"]
    assert event["document_type"] == "kanban_card"
    assert event["op"] == "updated"
    assert event["after"]["column_id"] == "done"


@pytest.mark.asyncio
async def test_rest_driven_move_is_not_double_dispatched(workflow_env, backend_client):
    """A persisted REST move emits its event once; draining again cannot rerun it."""
    store, state_reader = workflow_env

    queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            workflow_id = "workflow_rest_move"
            save_workflow_definition(
                store, _matching_workflow_definition(workflow_id, "REST move workflow")
            )

            card_id = "card_rest_1"
            store.save_document(
                PROGENITOR_STATE_PREFIX,
                f"kanban_card:{card_id}",
                {
                    "id": card_id,
                    "title": "REST card",
                    "summary": "Rest summary",
                    "column_id": "backlog",
                    "status": "idle",
                    "owner": "user",
                },
            )
            board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
            structure = (
                dict(board.payload) if board is not None and isinstance(board.payload, dict) else {}
            )
            order = list(structure.get("card_order") or [])
            if card_id not in order:
                order.append(card_id)
            structure["card_order"] = order
            store.save_document(PROGENITOR_STATE_PREFIX, "kanban_board", structure)
            # The app's own dispatcher drains on the TestClient loop; draining
            # from this loop too would share its lock across event loops.
            await asyncio.sleep(0.3)
            assert list_workflow_runs(store, workflow_id=workflow_id) == []

            request_id = "rest-kanban-move"
            patched = client.patch(
                f"/api/ui/kanban/cards/{card_id}",
                json={"column_id": "done"},
                headers={"X-Request-ID": request_id},
            )
            assert patched.status_code == 202
            assert patched.json() == {
                "request_id": request_id,
                "event_types": ["kanban.snapshot"],
            }
            events = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
            assert events
            snapshot_events = [event for event in events if event.type == "kanban.snapshot"]
            assert snapshot_events
            assert all(event.payload.get("request_id") == request_id for event in snapshot_events)
            snapshot = snapshot_events[0]
            card = next(card for card in snapshot.payload["cards"] if card["id"] == card_id)
            assert card["column_id"] == "done"

            await _wait_for_completed_run(store, workflow_id)

            # The app's live dispatcher (running on the TestClient loop) keeps
            # draining; the persisted cursor means the move never runs twice.
            await asyncio.sleep(0.3)

            items = list_workflow_runs(store, workflow_id=workflow_id)
            matches = [item.model_dump(mode="json") for item in items]
            assert len(matches) == 1
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)


@pytest.mark.asyncio
async def test_non_matching_move_does_not_fire_workflow(workflow_env, backend_client):
    """A card update that does not satisfy the event node's match must not start a run."""
    store, _state_reader = workflow_env
    workflow_id = "workflow_no_match"
    save_workflow_definition(store, _matching_workflow_definition(workflow_id, "No match workflow"))

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:card_no_match",
        {
            "id": "card_no_match",
            "title": "No match card",
            "summary": "No match summary",
            "column_id": "backlog",
            "status": "idle",
            "owner": "user",
        },
    )
    await events_dispatch.get_document_event_dispatcher().drain()

    # Update a field other than column_id — match requires after.column_id == done.
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "kanban_card:card_no_match",
        {
            "id": "card_no_match",
            "title": "No match card updated",
            "summary": "No match summary",
            "column_id": "backlog",
            "status": "running",
            "owner": "user",
        },
    )
    await events_dispatch.get_document_event_dispatcher().drain()

    assert list_workflow_runs(store, workflow_id=workflow_id) == []
