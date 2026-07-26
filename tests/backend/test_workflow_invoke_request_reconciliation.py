"""Durable Progenitor workflow invocation requests start backend runs."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.backend.document_store import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.models import AgentResponse
from app.backend.state_reader import StateReader
from app.backend.workflow_service import list_workflow_runs, save_workflow_definition
from app.progenitor.workflows import (
    MAX_WORKFLOW_INVOKE_DEPTH,
    default_workflow_definition,
    workflow_invoke_request_key,
)


@pytest.fixture
def workflow_env(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    monkeypatch.setattr(
        "app.backend.main.configure_default_document_store", lambda *_a, **_k: store
    )
    state_reader = StateReader(document_store=store)
    monkeypatch.setattr("app.backend.ui_routes.get_state_reader", lambda: state_reader)
    import app.backend.ui_routes as ui_routes

    ui_routes._workflow_executor = None
    ui_routes._workflow_invoke_watch_callback = None
    yield store
    ui_routes._workflow_executor = None
    ui_routes._workflow_invoke_watch_callback = None
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def backend_client(monkeypatch):
    import app.backend.main as backend_main

    mock_client = AsyncMock()
    mock_client.send_message.return_value = AgentResponse(
        response="workflow response", reasoning=None, reasoning_tokens=None
    )
    monkeypatch.setattr("app.backend.main.get_sse_client", lambda *_a, **_k: mock_client)
    backend_main.client = mock_client
    return mock_client


def _write_invoke_request(
    store: InMemoryDocumentStore,
    request_id: str,
    workflow_id: str,
    *,
    ancestry: list[str] | None = None,
    input: dict | None = None,
) -> str:
    key = workflow_invoke_request_key(request_id)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        key,
        {
            "request_id": request_id,
            "workflow_id": workflow_id,
            "input": input or {},
            "ancestry": ancestry or [],
            "requested_at": "2026-07-26T00:00:00+00:00",
        },
    )
    return key


async def _wait_for_completed_run(store: InMemoryDocumentStore, workflow_id: str) -> dict:
    deadline = time.time() + 2
    while time.time() < deadline:
        runs = list_workflow_runs(store, workflow_id=workflow_id)
        if runs and runs[0].status in {"completed", "failed"}:
            return runs[0].model_dump(mode="json")
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for workflow run completion for {workflow_id}")


async def test_invoke_request_starts_progenitor_sourced_run(workflow_env, backend_client):
    store = workflow_env
    workflow_id = "workflow_progenitor_invoke"
    save_workflow_definition(store, default_workflow_definition(workflow_id=workflow_id))
    key = _write_invoke_request(store, "request-1", workflow_id, input={"value": 1})

    from app.backend.ui_routes import _reconcile_workflow_invoke_requests

    await _reconcile_workflow_invoke_requests(key)
    run = await _wait_for_completed_run(store, workflow_id)
    assert run["source"] == "progenitor"
    assert run["ancestry"] == []
    assert run["trigger_event"]["payload"]["input"] == {"value": 1}
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_invoke_request_rejects_cycle(workflow_env, backend_client):
    store = workflow_env
    workflow_id = "workflow_cycle"
    save_workflow_definition(store, default_workflow_definition(workflow_id=workflow_id))
    key = _write_invoke_request(store, "request-cycle", workflow_id, ancestry=[workflow_id])

    from app.backend.ui_routes import _reconcile_workflow_invoke_requests

    await _reconcile_workflow_invoke_requests(key)
    assert list_workflow_runs(store, workflow_id=workflow_id) == []
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_invoke_request_rejects_max_depth(workflow_env, backend_client):
    store = workflow_env
    workflow_id = "workflow-depth"
    save_workflow_definition(store, default_workflow_definition(workflow_id=workflow_id))
    key = _write_invoke_request(
        store,
        "request-depth",
        workflow_id,
        ancestry=["wf_a", "wf_b", "wf_c"],
    )

    from app.backend.ui_routes import _reconcile_workflow_invoke_requests

    await _reconcile_workflow_invoke_requests(key)
    assert len([*list_workflow_runs(store, workflow_id=workflow_id)]) == 0
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None
    assert len(["wf_a", "wf_b", "wf_c"]) == MAX_WORKFLOW_INVOKE_DEPTH


async def test_malformed_invoke_request_is_dropped(workflow_env, backend_client):
    store = workflow_env
    key = workflow_invoke_request_key("bad")
    store.save_document(PROGENITOR_STATE_PREFIX, key, {"request_id": "bad"})

    from app.backend.ui_routes import _reconcile_workflow_invoke_requests

    await _reconcile_workflow_invoke_requests(key)
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None
