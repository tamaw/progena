import asyncio

import pytest
from ack_helpers import assert_acknowledged
from fastapi.testclient import TestClient

from app.backend.document_store import InMemoryDocumentStore, set_default_document_store
from app.backend.main import app
from app.backend.workflow_service import get_workflow_definition


@pytest.fixture
def workflow_env(monkeypatch):
    store = InMemoryDocumentStore()
    set_default_document_store(store)
    monkeypatch.setattr(
        "app.backend.main.configure_default_document_store", lambda *_args, **_kwargs: store
    )
    import app.backend.ui_routes as ui_routes

    ui_routes._workflow_executor = None
    ui_routes._ui_event_replay.clear()
    yield store
    ui_routes._workflow_executor = None
    ui_routes._ui_event_replay.clear()
    set_default_document_store(InMemoryDocumentStore())


def _drain(queue):
    return [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]


def test_workflow_create_acknowledges_then_emits_canonical_state(workflow_env):
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/ui/workflows",
                json={"title": "Prompt workflow"},
                headers={"X-Request-ID": "workflow-create"},
            )
        assert_acknowledged(
            response, ["workflow.updated", "workflows.snapshot"], request_id="workflow-create"
        )
        events = _drain(queue)
        workflow = events[0].payload["workflow"]
        assert len(workflow["graph"]["nodes"]) == 2
        kinds = {node["kind"] for node in workflow["graph"]["nodes"].values()}
        assert kinds == {"event", "agent"}
        assert len(workflow["graph"]["edges"]) == 1
        assert workflow["graph"]["edges"][0]["kind"] == "control"
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_workflow_content_round_trip_keeps_typed_schemas(workflow_env):
    from app.backend.workflow_service import save_workflow_definition
    from app.progenitor.workflows import default_workflow_definition

    payload = default_workflow_definition(title="Typed schemas")
    agent_id = next(
        node_id for node_id, node in payload["graph"]["nodes"].items() if node["kind"] == "agent"
    )
    payload["graph"]["nodes"][agent_id]["input_schema"] = {
        "type": "object",
        "required": ["card"],
        "properties": {"card": {"title": "Card", "type": "object"}},
    }
    event_id = next(
        node_id for node_id, node in payload["graph"]["nodes"].items() if node["kind"] == "event"
    )
    payload["graph"]["edges"] = [
        {
            "kind": "control",
            "from": {"node": event_id, "port": "default"},
            "to": {"node": agent_id, "port": "default"},
        },
        {
            "kind": "data",
            "from": {"node": event_id, "port": "card"},
            "to": {"node": agent_id, "port": "card"},
        },
    ]

    saved = save_workflow_definition(workflow_env, payload)
    assert saved.graph.nodes[agent_id].input_schema["required"] == ["card"]
