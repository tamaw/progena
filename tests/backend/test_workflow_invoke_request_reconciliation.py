"""Durable Progenitor workflow invocation requests start backend runs."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

import app.backend.lifecycle.state as lifecycle_state
import app.backend.lifecycle.watchers as lifecycle_watchers
import app.backend.workflows.runtime as workflows_runtime
from app.backend.chat.models import AgentResponse
from app.backend.events.documents import seed_document_types
from app.backend.monitoring.state import StateReader
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    InMemoryDocumentStore,
    set_default_document_store,
)
from app.backend.workflows.service import list_workflow_runs, save_workflow_definition
from app.progenitor.workflows.documents import (
    default_workflow_definition,
    workflow_invoke_request_key,
)


@pytest.fixture
def workflow_env(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    monkeypatch.setattr(
        "app.backend.storage.documents.configure_default_document_store", lambda *_a, **_k: store
    )
    state_reader = StateReader(document_store=store)
    monkeypatch.setattr("app.backend.monitoring.state.get_state_reader", lambda: state_reader)

    workflows_runtime._workflow_executor = None
    lifecycle_watchers._workflow_invoke_watch_callback = None
    yield store
    workflows_runtime._workflow_executor = None
    lifecycle_watchers._workflow_invoke_watch_callback = None
    set_default_document_store(InMemoryDocumentStore())


@pytest.fixture
def backend_client(monkeypatch):

    mock_client = AsyncMock()
    mock_client.send_message.return_value = AgentResponse(
        response="workflow response", reasoning=None, reasoning_tokens=None
    )
    monkeypatch.setattr(
        "app.backend.integrations.runtime.get_sse_client", lambda *_a, **_k: mock_client
    )
    lifecycle_state.client = mock_client
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


async def test_invoke_request_runs_the_connected_agent_with_typed_trigger_ports(
    workflow_env, backend_client
):
    store = workflow_env
    workflow_id = "workflow_progenitor_invoke"
    definition = save_workflow_definition(
        store, default_workflow_definition(workflow_id=workflow_id)
    )
    key = _write_invoke_request(
        store, "request-1", workflow_id, input={"key": "kanban_card:a", "id": "a"}
    )

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
    run = await _wait_for_completed_run(store, workflow_id)
    assert run["status"] == "completed", run["last_error"]
    assert run["source"] == "progenitor"
    assert run["ancestry"] == []
    assert run["trigger_event"]["key"] == "kanban_card:a"
    # The agent consuming the event's ``key`` port actually ran.
    backend_client.send_message.assert_awaited()
    agent_ids = [nid for nid, node in definition.graph.nodes.items() if node.kind == "agent"]
    assert agent_ids and all(nid in run["node_outputs"] for nid in agent_ids)
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_invoke_request_with_input_outside_trigger_ports_is_refused(
    workflow_env, backend_client
):
    store = workflow_env
    workflow_id = "workflow_bad_input"
    save_workflow_definition(store, default_workflow_definition(workflow_id=workflow_id))
    key = _write_invoke_request(store, "request-bad", workflow_id, input={"key": 42})

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
    assert list_workflow_runs(store, workflow_id=workflow_id) == []
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_invoke_request_rejects_cycle(workflow_env, backend_client):
    store = workflow_env
    workflow_id = "workflow_cycle"
    save_workflow_definition(store, default_workflow_definition(workflow_id=workflow_id))
    key = _write_invoke_request(store, "request-cycle", workflow_id, ancestry=[workflow_id])

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
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

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
    assert len([*list_workflow_runs(store, workflow_id=workflow_id)]) == 0
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_malformed_invoke_request_is_dropped(workflow_env, backend_client):
    store = workflow_env
    key = workflow_invoke_request_key("bad")
    store.save_document(PROGENITOR_STATE_PREFIX, key, {"request_id": "bad"})

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is None


async def test_failed_invoke_request_is_retained_for_retry(workflow_env, monkeypatch):
    store = workflow_env
    save_workflow_definition(store, default_workflow_definition(workflow_id="workflow-transient"))
    key = _write_invoke_request(store, "request-retry", "workflow-transient")

    class FailingExecutor:
        async def trigger_workflow(self, *_args, **_kwargs):
            raise RuntimeError("temporary executor failure")

    monkeypatch.setattr(
        "app.backend.workflows.runtime.get_workflow_executor",
        lambda: FailingExecutor(),
    )

    await workflows_runtime._reconcile_workflow_invoke_requests(key)
    assert store.get_document(PROGENITOR_STATE_PREFIX, key, None) is not None
