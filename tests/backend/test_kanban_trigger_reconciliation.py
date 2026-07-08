"""Kanban workflow triggers fire for changes made outside the REST routes.

The runtime agent's actual mechanism for touching kanban cards is writing
straight to /state/tasks/<id>.md, which persists via StateManager directly to
the shared Postgres store -- it never goes through POST/PATCH/DELETE
/api/ui/kanban/cards. These tests simulate that by writing store documents
directly (bypassing the REST routes) and driving the same store-change
notification the runtime's write would produce, then asserting the
now-generic _reconcile_kanban_card_triggers watcher fires the workflow the
REST-driven path already covers in test_workflow_routes.py.
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.main import app
from app.backend.models import AgentResponse
from app.backend.state_reader import StateReader


@pytest.fixture
def workflow_env(tmp_path, monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    monkeypatch.setattr(
        "app.backend.main.configure_default_document_store", lambda *_a, **_k: store
    )

    state_dir = tmp_path / "data" / "state"
    config_dir = tmp_path / "data" / "config"
    sessions_dir = tmp_path / "data" / "sessions"
    logs_dir = tmp_path / "logs"
    for directory in (state_dir, config_dir, sessions_dir, logs_dir):
        directory.mkdir(parents=True)

    state_reader = StateReader(
        state_dir=state_dir,
        config_dir=config_dir,
        sessions_dir=sessions_dir,
        logs_dir=logs_dir,
        document_store=store,
    )
    monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)

    import app.backend.ui_routes as ui_routes

    ui_routes._workflow_executor = None
    ui_routes._ui_event_replay.clear()
    ui_routes._last_known_kanban_cards.clear()
    ui_routes._kanban_card_cache_seeded = False
    ui_routes._state_watch_callback = None
    ui_routes._kanban_trigger_watch_callback = None
    yield store, state_reader
    ui_routes._workflow_executor = None
    ui_routes._ui_event_replay.clear()
    ui_routes._last_known_kanban_cards.clear()
    ui_routes._kanban_card_cache_seeded = False
    ui_routes._state_watch_callback = None
    ui_routes._kanban_trigger_watch_callback = None
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def backend_client(monkeypatch):
    import app.backend.main as backend_main

    mock_client = AsyncMock()
    mock_client.send_message.return_value = AgentResponse(
        response="workflow response",
        reasoning=None,
        reasoning_tokens=None,
    )
    monkeypatch.setattr("app.backend.main.get_sse_client", lambda *_a, **_k: mock_client)
    backend_main.client = mock_client
    yield mock_client


def _write_agent_card(store: InMemoryDocumentStore, card_id: str, **fields) -> dict:
    """Write a kanban_card:<id> row + board card_order the way StateManager's
    save_kanban_card does -- i.e. the runtime agent's actual write path, not
    the REST routes.
    """
    card = {
        "id": card_id,
        "title": "Agent-authored card",
        "column_id": "backlog",
        "status": "idle",
        "owner": "user",
        **fields,
    }
    store.save_document(PROGENITOR_STATE_PREFIX, f"kanban_card:{card_id}", card)
    board = store.get_document(PROGENITOR_STATE_PREFIX, "kanban_board", None)
    structure = dict(board.payload) if board is not None and isinstance(board.payload, dict) else {}
    order = list(structure.get("card_order") or [])
    if card_id not in order:
        order.append(card_id)
    structure["card_order"] = order
    store.save_document(PROGENITOR_STATE_PREFIX, "kanban_board", structure)
    return card


async def _wait_for_completed_run(
    client: TestClient, workflow_id: str, *, timeout: float = 2.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = client.get("/api/ui/workflow-runs").json()["items"]
        matches = [item for item in items if item["workflow_id"] == workflow_id]
        if matches and matches[0]["status"] in {"completed", "failed"}:
            return matches[0]
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for workflow run completion for {workflow_id}")


async def test_agent_card_move_fires_matching_workflow(workflow_env, backend_client):
    store, state_reader = workflow_env
    with TestClient(app) as client:
        created = client.post(
            "/api/ui/workflows",
            json={"title": "Agent move workflow", "event_type": "kanban.card.moved"},
        )
        workflow_id = created.json()["workflow"]["id"]

        # First write: the agent creates the card directly (bypassing REST).
        # This should seed the reconciler's baseline, not fire a trigger.
        _write_agent_card(store, "card_agent_1", column_id="backlog")
        state_reader._on_store_change(
            '{"prefix": "progenitor.state", "key": "kanban_card:card_agent_1"}'
        )
        await asyncio.sleep(0.05)
        assert client.get("/api/ui/workflow-runs").json()["items"] == []

        # Second write: the agent moves the card to done, still via a direct
        # store write -- this is the case that used to never fire triggers.
        _write_agent_card(store, "card_agent_1", column_id="done")
        state_reader._on_store_change(
            '{"prefix": "progenitor.state", "key": "kanban_card:card_agent_1"}'
        )

        run = await _wait_for_completed_run(client, workflow_id)
        assert run["status"] == "completed"
        assert run["trigger_event"]["event_type"] == "kanban.card.moved"
        payload = run["trigger_event"]["payload"]
        assert payload["from"]["column_id"] == "backlog"
        assert payload["to"]["column_id"] == "done"


async def test_rest_driven_move_is_not_double_dispatched_by_reconciler(
    workflow_env, backend_client
):
    """A REST-driven move already dispatches inline; replaying the same
    store-change notification afterward (as the real NOTIFY listener would)
    must not fire a second run for the same change.
    """
    store, state_reader = workflow_env
    with TestClient(app) as client:
        created = client.post(
            "/api/ui/workflows",
            json={"title": "REST move workflow", "event_type": "kanban.card.moved"},
        )
        workflow_id = created.json()["workflow"]["id"]

        card = client.post(
            "/api/ui/kanban/cards",
            json={"title": "REST card"},
        ).json()

        patched = client.patch(
            f"/api/ui/kanban/cards/{card['id']}",
            json={"column_id": "done"},
        )
        assert patched.status_code == 200

        await _wait_for_completed_run(client, workflow_id)

        # Replay the notification the REST write itself would have produced.
        state_reader._on_store_change(
            f'{{"prefix": "progenitor.state", "key": "kanban_card:{card["id"]}"}}'
        )
        await asyncio.sleep(0.1)

        items = client.get("/api/ui/workflow-runs").json()["items"]
        matches = [item for item in items if item["workflow_id"] == workflow_id]
        assert len(matches) == 1
