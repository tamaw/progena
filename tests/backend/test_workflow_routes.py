import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from ack_helpers import assert_acknowledged
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

    state_reader = StateReader(document_store=store)
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
    monkeypatch.setattr("app.backend.main.get_sse_client", lambda *_a, **_k: mock_client)
    backend_main.client = mock_client
    yield mock_client


def _drain_envelopes(queue: asyncio.Queue) -> list:
    return [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]


def _wait_for_completed_run(client: TestClient, workflow_id: str, *, timeout: float = 2.0) -> dict:
    from app.backend.document_store import get_default_document_store
    from app.backend.workflow_service import list_workflow_runs

    deadline = time.time() + timeout
    while time.time() < deadline:
        client.get("/api/ui/workflows")
        runs = list_workflow_runs(get_default_document_store(), workflow_id=workflow_id)
        for run in runs:
            if run.status in {"completed", "failed"}:
                return run.model_dump(mode="json")
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for workflow run completion for {workflow_id}")


def test_workflow_crud_round_trip(workflow_env):
    """Persistence precedes request-correlated events and a data-free acknowledgement."""
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            create_request_id = "workflow-create-request"
            created = client.post(
                "/api/ui/workflows",
                json={"title": "Followup", "description": "Workflow under test"},
                headers={"X-Request-ID": create_request_id},
            )
            assert_acknowledged(
                created, ["workflow.updated", "workflows.snapshot"], request_id=create_request_id
            )
            envelopes = _drain_envelopes(queue)
            assert [envelope.type for envelope in envelopes] == [
                "workflow.updated",
                "workflows.snapshot",
            ]
            assert all(
                envelope.payload["request_id"] == create_request_id for envelope in envelopes
            )
            updated = envelopes[0]
            workflow_id = updated.payload["workflow"]["id"]
            path = updated.payload["path"]
            assert path == f"/config/workflows/{workflow_id}.json"

            list_request_id = "workflow-list-request"
            listed = client.get(
                "/api/ui/workflows",
                headers={"X-Request-ID": list_request_id},
            )
            assert_acknowledged(listed, ["workflows.snapshot"], request_id=list_request_id)
            snapshot = _drain_envelopes(queue)
            assert len(snapshot) == 1
            assert snapshot[0].type == "workflows.snapshot"
            assert snapshot[0].payload["request_id"] == list_request_id
            assert snapshot[0].payload["items"][0]["id"] == workflow_id

            load_request_id = "workflow-load-request"
            loaded = client.get(
                "/api/ui/workflows/content",
                params={"path": path},
                headers={"X-Request-ID": load_request_id},
            )
            assert_acknowledged(loaded, ["workflow.updated"], request_id=load_request_id)
            loaded_update = _drain_envelopes(queue)
            assert len(loaded_update) == 1
            assert loaded_update[0].type == "workflow.updated"
            assert loaded_update[0].payload["request_id"] == load_request_id
            assert loaded_update[0].payload["workflow"]["title"] == "Followup"

            updated_workflow = loaded_update[0].payload["workflow"]
            updated_workflow["title"] = "Followup v2"
            save_request_id = "workflow-save-request"
            saved = client.put(
                "/api/ui/workflows/content",
                json={"path": path, "workflow": updated_workflow},
                headers={"X-Request-ID": save_request_id},
            )
            assert_acknowledged(
                saved, ["workflow.updated", "workflows.snapshot"], request_id=save_request_id
            )
            saved_events = _drain_envelopes(queue)
            assert [event.type for event in saved_events] == [
                "workflow.updated",
                "workflows.snapshot",
            ]
            assert all(event.payload["request_id"] == save_request_id for event in saved_events)
            assert saved_events[0].payload["workflow"]["title"] == "Followup v2"

            delete_request_id = "workflow-delete-request"
            deleted = client.delete(
                f"/api/ui/workflows/{workflow_id}",
                headers={"X-Request-ID": delete_request_id},
            )
            assert_acknowledged(
                deleted, ["workflow.deleted", "workflows.snapshot"], request_id=delete_request_id
            )
            deleted_events = _drain_envelopes(queue)
            assert [event.type for event in deleted_events] == [
                "workflow.deleted",
                "workflows.snapshot",
            ]
            assert all(event.payload["request_id"] == delete_request_id for event in deleted_events)
            assert deleted_events[0].payload["workflow_id"] == workflow_id
            assert deleted_events[1].payload["items"] == []
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_workflow_put_rejects_cycle(workflow_env):
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/ui/workflows", json={"title": "Cycle check"})
            assert_acknowledged(
                created,
                ["workflow.updated", "workflows.snapshot"],
                request_id=created.json()["request_id"],
            )

            envelopes = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
            updated = next(e for e in envelopes if e.type == "workflow.updated")
            workflow = updated.payload["workflow"]
            path = updated.payload["path"]

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
                "size": {
                    "data": {"width": 340, "height": 160},
                    "settings": {"width": 340, "height": 280},
                },
            }

            request_id = "a0cd5a4d-2b1b-4bcb-8be2-3ba4c376275d"
            response = client.put(
                "/api/ui/workflows/content",
                json={"path": path, "workflow": workflow},
                headers={"X-Request-ID": request_id},
            )

            assert response.status_code == 400
            assert response.headers["X-Request-ID"] == request_id
            assert "acyclic" in response.json()["detail"]
            post_rejection_events = _drain_envelopes(queue)
            assert not any(
                event.type in {"workflow.updated", "workflows.snapshot"}
                for event in post_rejection_events
            )
            assert not any(
                event.payload.get("request_id") == request_id for event in post_rejection_events
            )
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_manual_trigger_reuses_same_thread_id_for_two_prompt_chain(workflow_env, backend_client):
    import app.backend.ui_routes as ui_routes

    backend_client.send_message.side_effect = [
        AgentResponse(response="first response", reasoning=None, reasoning_tokens=None),
        AgentResponse(response="second response", reasoning=None, reasoning_tokens=None),
    ]

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/ui/workflows",
                json={"title": "Two prompt chain"},
                headers={"X-Request-ID": "two-prompt-create"},
            )
            assert_acknowledged(
                created, ["workflow.updated", "workflows.snapshot"], request_id="two-prompt-create"
            )

            updated = next(e for e in _drain_envelopes(queue) if e.type == "workflow.updated")
            workflow = updated.payload["workflow"]
            path = updated.payload["path"]

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
                "size": {
                    "data": {"width": 340, "height": 160},
                    "settings": {"width": 340, "height": 280},
                },
            }
            saved = client.put(
                "/api/ui/workflows/content",
                json={"path": path, "workflow": workflow},
                headers={"X-Request-ID": "two-prompt-save"},
            )
            assert_acknowledged(
                saved, ["workflow.updated", "workflows.snapshot"], request_id="two-prompt-save"
            )
            _drain_envelopes(queue)

            trigger_request_id = "two-prompt-trigger"
            trigger = client.post(
                f"/api/ui/workflows/{workflow['id']}/trigger",
                json={},
                headers={"X-Request-ID": trigger_request_id},
            )
            assert_acknowledged(trigger, ["workflow.run.started"], request_id=trigger_request_id)
            trigger_event = next(
                event
                for event in _drain_envelopes(queue)
                if event.type == "workflow.run.started"
                and event.payload["request_id"] == trigger_request_id
            )
            assert trigger_event.payload["workflow_id"] == workflow["id"]

            run = _wait_for_completed_run(client, workflow["id"])
            assert run["status"] == "completed"
            assert run["completed_node_ids"] == [event_node_id, prompt_a_id, prompt_b_id]
            assert backend_client.send_message.await_count == 2
            first_metadata = backend_client.send_message.await_args_list[0].kwargs["metadata"]
            second_metadata = backend_client.send_message.await_args_list[1].kwargs["metadata"]
            assert first_metadata["thread_id"] == second_metadata["thread_id"]
            assert run["thread_id"] == first_metadata["thread_id"]
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_manual_trigger_of_active_workflow_emits_real_active_run(workflow_env):
    """A queued manual trigger reports the run that is actually active, not a synthetic one."""
    import app.backend.ui_routes as ui_routes
    from app.backend.workflow_service import save_workflow_run
    from app.progenitor.workflows import WorkflowRun

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/ui/workflows",
                json={"title": "Already active workflow"},
                headers={"X-Request-ID": "active-workflow-create"},
            )
            assert_acknowledged(
                created,
                ["workflow.updated", "workflows.snapshot"],
                request_id="active-workflow-create",
            )
            workflow = next(
                event for event in _drain_envelopes(queue) if event.type == "workflow.updated"
            ).payload["workflow"]
            active_run = WorkflowRun(
                run_id="workflow_run_already_active",
                workflow_id=workflow["id"],
                status="paused",
                current_node_id="prompt_active",
                completed_node_ids=["event_active"],
                thread_id="workflow-thread-active",
                trigger_event={
                    "event_type": "manual",
                    "payload": {"source": "existing active run"},
                    "request_id": "original-active-request",
                },
                last_output="Awaiting operator review.",
                queued_at="2026-07-13T10:00:00+00:00",
                started_at="2026-07-13T10:00:01+00:00",
                updated_at="2026-07-13T10:00:02+00:00",
            )
            save_workflow_run(workflow_env, active_run)

            request_id = "active-workflow-trigger"
            triggered = client.post(
                f"/api/ui/workflows/{workflow['id']}/trigger",
                json={"payload": {"operator": "tester"}},
                headers={"X-Request-ID": request_id},
            )
            assert_acknowledged(triggered, ["workflow.run.started"], request_id=request_id)
            event = next(
                envelope
                for envelope in _drain_envelopes(queue)
                if envelope.type == "workflow.run.started"
            )

        assert event.payload == {
            "workflow_id": workflow["id"],
            "run_id": active_run.run_id,
            "status": "paused",
            "run": active_run.model_dump(mode="json"),
            "request_id": request_id,
        }
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_kanban_create_event_triggers_matching_workflow(workflow_env, backend_client):
    """A kanban write persists, broadcasts a correlated snapshot, and triggers its workflow."""
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/ui/workflows",
                json={"title": "Kanban created workflow", "event_type": "kanban.card.created"},
                headers={"X-Request-ID": "kanban-workflow-create"},
            )
            assert_acknowledged(
                created,
                ["workflow.updated", "workflows.snapshot"],
                request_id="kanban-workflow-create",
            )
            updated = next(e for e in _drain_envelopes(queue) if e.type == "workflow.updated")
            workflow_id = updated.payload["workflow"]["id"]

            request_id = "kanban-card-create"
            response = client.post(
                "/api/ui/kanban/cards",
                json={"title": "Workflow card", "summary": "Triggers workflow"},
                headers={"X-Request-ID": request_id},
            )
            assert_acknowledged(response, ["kanban.snapshot"], request_id=request_id)
            snapshot = next(
                event for event in _drain_envelopes(queue) if event.type == "kanban.snapshot"
            )
            assert snapshot.payload["request_id"] == request_id
            assert any(card["title"] == "Workflow card" for card in snapshot.payload["cards"])

            run = _wait_for_completed_run(client, workflow_id)
            assert run["status"] == "completed"
            assert run["trigger_event"]["event_type"] == "kanban.card.created"
            assert backend_client.send_message.await_count == 1
    finally:
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_workflow_routes_broadcast_ui_events(workflow_env, backend_client):
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/ui/workflows", json={"title": "Broadcast workflow"})
            assert_acknowledged(
                created,
                ["workflow.updated", "workflows.snapshot"],
                request_id=created.json()["request_id"],
            )

            envelopes = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
            updated = next(e for e in envelopes if e.type == "workflow.updated")
            workflow_id = updated.payload["workflow"]["id"]
            event_types = [e.type for e in envelopes]
            assert "workflow.updated" in event_types
            assert "workflows.snapshot" in event_types

            triggered = client.post(f"/api/ui/workflows/{workflow_id}/trigger", json={})
            assert_acknowledged(
                triggered, ["workflow.run.started"], request_id=triggered.json()["request_id"]
            )
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
        ui_routes._ui_stream_subscribers.pop(queue, None)


def test_workflow_updated_broadcast_serializes_edges_by_alias(workflow_env, backend_client):
    # Regression: WorkflowEdge.from_ is a Pydantic field aliased to the JSON
    # key "from" (a reserved word). model_dump(mode="json") without
    # by_alias=True silently emits "from_" instead, so the frontend's
    # edge.from.node access reads undefined and crashes. broadcast_ui_event
    # calls must always pass by_alias=True when dumping a WorkflowDefinition.
    import app.backend.ui_routes as ui_routes

    queue = asyncio.Queue()
    ui_routes._ui_stream_subscribers[queue] = None
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
        ui_routes._ui_stream_subscribers.pop(queue, None)
