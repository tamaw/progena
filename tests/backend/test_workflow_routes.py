import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.backend.document_store import InMemoryDocumentStore, set_default_document_store
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
    yield store
    ui_routes._workflow_executor = None
    ui_routes._ui_event_replay.clear()
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
    monkeypatch.setattr("app.backend.main.get_ipc_client", lambda *_a, **_k: mock_client)
    backend_main.client = mock_client
    yield mock_client


def _wait_for_completed_run(client: TestClient, workflow_id: str, *, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = client.get("/api/ui/workflow-runs").json()["items"]
        matches = [item for item in items if item["workflow_id"] == workflow_id]
        if matches and matches[0]["status"] in {"completed", "failed"}:
            return matches[0]
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for workflow run completion for {workflow_id}")


def test_workflow_crud_round_trip(workflow_env):
    with TestClient(app) as client:
        created = client.post(
            "/api/ui/workflows",
            json={"title": "Followup", "description": "Workflow under test"},
        )
        assert created.status_code == 200
        payload = created.json()
        workflow_id = payload["workflow"]["id"]
        assert payload["path"] == f"/state/workflows/{workflow_id}.json"

        listed = client.get("/api/ui/workflows")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["id"] == workflow_id

        loaded = client.get(
            "/api/ui/workflows/content",
            params={"path": f"/state/workflows/{workflow_id}.json"},
        )
        assert loaded.status_code == 200
        assert loaded.json()["workflow"]["title"] == "Followup"

        updated_workflow = loaded.json()["workflow"]
        updated_workflow["title"] = "Followup v2"
        saved = client.put(
            "/api/ui/workflows/content",
            json={"path": f"/state/workflows/{workflow_id}.json", "workflow": updated_workflow},
        )
        assert saved.status_code == 200
        assert saved.json()["workflow"]["title"] == "Followup v2"

        deleted = client.delete(f"/api/ui/workflows/{workflow_id}")
        assert deleted.status_code == 204
        assert client.get("/api/ui/workflows").json()["items"] == []


def test_workflow_put_rejects_cycle(workflow_env):
    with TestClient(app) as client:
        created = client.post("/api/ui/workflows", json={"title": "Cycle check"}).json()
        workflow = created["workflow"]
        _event_node_id, prompt_a_id = list(workflow["graph"]["nodes"])
        prompt_b_id = "prompt_cycle"
        workflow["graph"]["nodes"][prompt_b_id] = {
            "kind": "prompt",
            "title": "Cycle prompt",
            "prompt": "Create a cycle",
            "agent_name": "progenitor",
            "role": None,
        }
        workflow["graph"]["edges"].append(
            {
                "from": {"node": prompt_a_id, "port": "default"},
                "to": {"node": prompt_b_id, "port": "default"},
            }
        )
        workflow["graph"]["edges"].append(
            {
                "from": {"node": prompt_b_id, "port": "default"},
                "to": {"node": prompt_a_id, "port": "default"},
            }
        )
        workflow["ui"]["nodes"][prompt_b_id] = {
            "position": {"x": 816, "y": 160},
            "size": {"width": 320, "height": 220},
        }

        response = client.put(
            "/api/ui/workflows/content",
            json={"path": created["path"], "workflow": workflow},
        )

        assert response.status_code == 400
        assert "acyclic" in response.json()["detail"]


def test_manual_trigger_reuses_same_thread_id_for_two_prompt_chain(workflow_env, backend_client):
    backend_client.send_message.side_effect = [
        AgentResponse(response="first response", reasoning=None, reasoning_tokens=None),
        AgentResponse(response="second response", reasoning=None, reasoning_tokens=None),
    ]

    with TestClient(app) as client:
        created = client.post("/api/ui/workflows", json={"title": "Two prompt chain"}).json()
        workflow = created["workflow"]
        event_node_id, prompt_a_id = list(workflow["graph"]["nodes"])
        prompt_b_id = "prompt_second"
        workflow["graph"]["nodes"][prompt_a_id]["prompt"] = "First prompt"
        workflow["graph"]["nodes"][prompt_b_id] = {
            "kind": "prompt",
            "title": "Second prompt",
            "prompt": "Second prompt",
            "agent_name": "progenitor",
            "role": None,
        }
        workflow["graph"]["edges"].append(
            {
                "from": {"node": prompt_a_id, "port": "default"},
                "to": {"node": prompt_b_id, "port": "default"},
            }
        )
        workflow["ui"]["nodes"][prompt_b_id] = {
            "position": {"x": 816, "y": 160},
            "size": {"width": 320, "height": 220},
        }
        saved = client.put(
            "/api/ui/workflows/content",
            json={"path": created["path"], "workflow": workflow},
        )
        assert saved.status_code == 200

        trigger = client.post(f"/api/ui/workflows/{workflow['id']}/trigger", json={})
        assert trigger.status_code == 200

        run = _wait_for_completed_run(client, workflow["id"])
        assert run["status"] == "completed"
        assert run["completed_node_ids"] == [event_node_id, prompt_a_id, prompt_b_id]
        assert backend_client.send_message.await_count == 2
        first_metadata = backend_client.send_message.await_args_list[0].kwargs["metadata"]
        second_metadata = backend_client.send_message.await_args_list[1].kwargs["metadata"]
        assert first_metadata["thread_id"] == second_metadata["thread_id"]
        assert run["thread_id"] == first_metadata["thread_id"]


def test_kanban_create_event_triggers_matching_workflow(workflow_env, backend_client):
    with TestClient(app) as client:
        created = client.post(
            "/api/ui/workflows",
            json={"title": "Kanban created workflow", "event_type": "kanban.card.created"},
        )
        workflow_id = created.json()["workflow"]["id"]

        response = client.post(
            "/api/ui/kanban/cards",
            json={"title": "Workflow card", "summary": "Triggers workflow"},
        )
        assert response.status_code == 200

        run = _wait_for_completed_run(client, workflow_id)
        assert run["status"] == "completed"
        assert run["trigger_event"]["event_type"] == "kanban.card.created"
        assert backend_client.send_message.await_count == 1


def test_workflow_routes_broadcast_ui_events(workflow_env, backend_client):
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers.append(queue)
    try:
        with TestClient(app) as client:
            created = client.post("/api/ui/workflows", json={"title": "Broadcast workflow"})
            assert created.status_code == 200
            workflow_id = created.json()["workflow"]["id"]

            envelopes = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
            event_types = [envelope.type for envelope in envelopes]
            assert "workflow.updated" in event_types
            assert "workflows.snapshot" in event_types

            triggered = client.post(f"/api/ui/workflows/{workflow_id}/trigger", json={})
            assert triggered.status_code == 200
            _wait_for_completed_run(client, workflow_id)

            deadline = time.time() + 2.0
            seen = set()
            while (
                time.time() < deadline
                and not {"workflow.run.started", "workflow.run.updated", "workflow.run.completed"}
                <= seen
            ):
                while queue.qsize():
                    seen.add(queue.get_nowait()["envelope"].type)
                if {
                    "workflow.run.started",
                    "workflow.run.updated",
                    "workflow.run.completed",
                } <= seen:
                    break
                time.sleep(0.02)

            assert {
                "workflow.run.started",
                "workflow.run.updated",
                "workflow.run.completed",
            } <= seen
    finally:
        ui_routes._ui_stream_subscribers.remove(queue)


def test_workflow_updated_broadcast_serializes_edges_by_alias(workflow_env, backend_client):
    # Regression: WorkflowEdge.from_ is a Pydantic field aliased to the JSON
    # key "from" (a reserved word). model_dump(mode="json") without
    # by_alias=True silently emits "from_" instead, so the frontend's
    # edge.from.node access reads undefined and crashes. broadcast_ui_event
    # calls must always pass by_alias=True when dumping a WorkflowDefinition.
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers.append(queue)
    try:
        with TestClient(app) as client:
            client.post("/api/ui/workflows", json={"title": "Alias check"})

            envelopes = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
            updated = next(e for e in envelopes if e.type == "workflow.updated")
            edges = updated.payload["workflow"]["graph"]["edges"]
            assert edges, "expected the default workflow scaffold to include an edge"
            for edge in edges:
                assert "from" in edge, f"edge serialized without aliasing: {edge!r}"
                assert "from_" not in edge
                assert "node" in edge["from"]
                assert "node" in edge["to"]
    finally:
        ui_routes._ui_stream_subscribers.remove(queue)
