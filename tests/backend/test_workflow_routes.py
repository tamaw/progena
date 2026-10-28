import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

import src.backend.events.stream as events_stream
import src.backend.workflows.routes as workflows_routes
import src.backend.workflows.runtime as workflows_runtime
from src.backend.events.documents import seed_document_types
from src.backend.main import app
from src.backend.storage.documents import InMemoryDocumentStore, set_default_document_store


@pytest.fixture
def workflow_env(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    seed_document_types(store)
    monkeypatch.setattr(
        "src.backend.storage.documents.configure_default_document_store",
        lambda *_args, **_kwargs: store,
    )

    workflows_runtime._workflow_executor = None
    events_stream._ui_event_replay.clear()
    yield store
    workflows_runtime._workflow_executor = None
    events_stream._ui_event_replay.clear()
    set_default_document_store(InMemoryDocumentStore())


def _drain(queue):
    return [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]


def test_workflow_create_acknowledges_then_emits_canonical_state(workflow_env):

    queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/ui/workflows",
                json={"title": "Prompt workflow"},
                headers={"X-Operation-Id": "workflow-create"},
            )
            events = _drain(queue)
        assert_acknowledged(
            response, ["workflow.updated", "workflows.snapshot"], request_id="workflow-create"
        )
        workflow = events[0].payload["workflow"]
        assert len(workflow["graph"]["nodes"]) == 2
        nodes = workflow["graph"]["nodes"]
        assert {node["kind"] for node in nodes.values()} == {"event", "agent"}
        (edge,) = workflow["graph"]["edges"]
        assert nodes[edge["from"]["node"]]["kind"] == "event"
        assert nodes[edge["to"]["node"]]["kind"] == "agent"
        assert edge["from"]["port"] == edge["to"]["port"] == "key"
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)


def test_cron_preview_returns_utc_instants(workflow_env, monkeypatch):
    _ = workflow_env
    monkeypatch.setattr(
        workflows_routes,
        "next_cron_time",
        lambda *_args: datetime(2026, 1, 1, 11, tzinfo=ZoneInfo("Australia/Sydney")),
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/ui/workflows/cron-preview",
            params={"expression": "0 0 * * *", "timezone": "Australia/Sydney", "count": 1},
        )

    assert response.status_code == 200
    assert response.json() == {"next_runs": ["2026-01-01T00:00:00Z"]}


def test_workflow_content_round_trip_keeps_typed_schemas(workflow_env):
    from src.backend.workflows.service import save_workflow_definition
    from src.progena.workflows.documents import default_workflow_definition

    payload = default_workflow_definition(title="Typed schemas")
    agent_id = next(
        node_id for node_id, node in payload["graph"]["nodes"].items() if node["kind"] == "agent"
    )
    payload["graph"]["nodes"][agent_id]["template"] = "Process card: {card}"
    payload["graph"]["nodes"]["data_card"] = {
        "kind": "data",
        "title": "Card",
        "value": {},
        "output_schema": {"type": "object"},
    }
    payload["graph"]["edges"] = [
        {
            "from": {"node": "data_card", "port": "value"},
            "to": {"node": agent_id, "port": "card"},
        },
    ]

    saved = save_workflow_definition(workflow_env, payload)
    assert saved.graph.nodes[agent_id].input_schema["required"] == ["card"]
