"""Tests for the backend-owned agent catalog routes.

These use a fake in-memory store so they do not require Postgres; the real
``AgentStore`` is exercised against a live database elsewhere.
"""

from __future__ import annotations

from typing import Any

import pytest
from ack_helpers import assert_acknowledged
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.backend.agents.routes import create_agent_profile_router, validate_agent_graph
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore
from src.progena.settings.policies import ExecutionPolicy

_DEFAULT_GRAPH = {
    "status": "Draft",
    "summary": None,
    "entry": "greet",
    "nodes": [
        {
            "id": "greet",
            "label": "Greet",
            "kind": "prompt",
            "output_key": "greet",
            "config": {"prompt_template": "do the thing: {request}"},
        },
        {
            "id": "gate",
            "label": "Gate",
            "kind": "eval",
            "output_key": "gate",
            "config": {"expression": "True"},
        },
    ],
    "edges": [{"from": "greet", "to": "gate", "level": "normal"}],
}


def _agent(agent_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": agent_id,
        "kind": "routine",
        "name": agent_id.title(),
        "handle": f"@{agent_id}",
        "group": "Routines",
        "color": "#2f6ff6",
        "model": "inherit",
        "role": "Test agent",
        "version": "v0.1.0",
        "summary": "A test agent",
        "status": "running",
        "prompt": "be helpful",
        "skills": [{"id": "demo", "name": "Demo", "description": "x", "tags": ["t"]}],
        "tools": [],
        "repositories": [],
        "knowledge": [],
        "memories": [],
        "sessions": [],
        "tasks": [],
        "metrics": {"invocations": "0"},
        "updated": None,
    }
    base.update(overrides)
    return base


class FakeAgentStore:
    def __init__(self) -> None:
        self._agents = {"alpha": _agent("alpha"), "beta": _agent("beta")}
        self._graphs: dict[str, dict[str, Any]] = {}

    def list_agents(self) -> list[dict[str, Any]]:
        return list(self._agents.values())

    def list_object_agents(self) -> list[dict[str, Any]]:
        return []

    def get_object_agent(self, agent_id: str) -> dict[str, Any] | None:
        return None

    def get_routine_document(self, agent_id: str) -> dict[str, Any] | None:
        return (
            self._graphs.get(agent_id, dict(_DEFAULT_GRAPH)) if agent_id in self._agents else None
        )

    def delete_routine_agent(self, agent_id: str) -> bool:
        self._graphs.pop(agent_id, None)
        return self._agents.pop(agent_id, None) is not None

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        agent = self._agents.get(agent_id)
        if agent is None:
            return None
        return {**agent, "graph": self._graphs.get(agent_id, dict(_DEFAULT_GRAPH))}

    def update_graph(
        self,
        agent_id: str,
        graph: dict[str, Any],
        *,
        status: str | None = None,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        if agent_id not in self._agents:
            return None
        payload = {
            "status": status or "Draft",
            "summary": summary,
            "entry": graph.get("entry"),
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        }
        self._graphs[agent_id] = payload
        return payload


def _install_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Operation-Id") or "test-middleware-request-id"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Operation-Id"] = request_id
        return response


@pytest.fixture
def harness(monkeypatch):
    store = FakeAgentStore()
    events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    # The list/detail routes read A2A/ACP agents through from Progena's
    # registry; keep tests hermetic by returning no remote agent profiles by
    # default.
    import src.backend.agents.routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [])
    monkeypatch.setattr(routes, "acp_profiles_from_registry", lambda: [])

    doc_store = InMemoryDocumentStore()
    doc_store.save_document(
        PROGENA_STATE_PREFIX,
        "policy:default",
        ExecutionPolicy(id="default", name="Default").model_dump(mode="json"),
    )
    app = FastAPI()
    app.state.doc_store = doc_store
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: store,
            get_document_store=lambda: doc_store,
            broadcast_ui_event=broadcast,
        ),
        prefix="/api/ui",
    )
    return TestClient(app), events


def test_list_publishes_request_correlated_snapshot(harness):
    """The accepted client ID resolves on the canonical agents snapshot."""
    client, events = harness
    request_id = "b6ae12d0-8c70-4e3d-883d-89c89a1a2049"

    response = client.get(
        "/api/ui/agent-profiles",
        headers={"X-Operation-Id": request_id},
    )

    assert_acknowledged(response, ["agents.snapshot"], request_id)
    snapshot_events = [
        (payload, kwargs)
        for event_type, payload, kwargs in events
        if event_type == "agents.snapshot"
    ]
    assert len(snapshot_events) == 1
    snapshot, event_kwargs = snapshot_events[0]
    assert event_kwargs["request_id"] == request_id
    assert snapshot["total"] == 2
    assert {agent["id"] for agent in snapshot["agents"]} == {"alpha", "beta"}


def test_list_includes_skills(harness):
    client, events = harness
    response = client.get("/api/ui/agent-profiles")
    assert_acknowledged(response, ["agents.snapshot"])
    snapshot_events = [(t, p) for t, p, _ in events if t == "agents.snapshot"]
    assert len(snapshot_events) == 1
    alpha = next(a for a in snapshot_events[0][1]["agents"] if a["id"] == "alpha")
    assert alpha["skills"] == [{"id": "demo", "name": "Demo", "description": "x", "tags": ["t"]}]


def test_detail_publishes_upsert_and_selected(harness):
    client, events = harness
    response = client.get(
        "/api/ui/agent-profiles/alpha",
        headers={"X-Operation-Id": "req-1"},
    )
    assert_acknowledged(response, ["agent.upserted", "agent.selected"], "req-1")
    types = [e[0] for e in events]
    assert "agent.upserted" in types
    selected = next(kwargs for t, p, kwargs in events if t == "agent.selected")
    assert selected["request_id"] == "req-1"


def test_detail_unknown_returns_404(harness):
    client, _ = harness
    assert client.get("/api/ui/agent-profiles/missing").status_code == 404


def test_graph_update_persists_and_broadcasts(harness):
    client, events = harness
    payload = {
        "nodes": [
            {
                "id": "a",
                "kind": "prompt",
                "output_key": "a",
                "config": {"prompt_template": "hi {request}"},
            },
            {"id": "b", "kind": "noop", "output_key": "b"},
        ],
        "edges": [{"from": "a", "to": "b", "level": "normal"}],
        "entry": "a",
        "status": "Designed",
    }
    response = client.patch("/api/ui/agent-profiles/alpha/graph", json=payload)
    assert_acknowledged(response, ["agent.graph.updated"])
    assert events[-1][0] == "agent.graph.updated"
    # Broadcast payload contains updated graph data
    graph_payload = events[-1][1]
    assert graph_payload["graph"]["status"] == "Designed"
    assert graph_payload["graph"]["entry"] == "a"
    # Persisted: subsequent detail broadcasts the new graph data
    detail_resp = client.get("/api/ui/agent-profiles/alpha")
    assert_acknowledged(detail_resp, ["agent.upserted", "agent.selected"])
    detail_upserted = next(p for t, p, _ in events if t == "agent.upserted")
    assert detail_upserted["agent"]["graph"]["status"] == "Designed"


def test_graph_update_invalid_returns_422(harness):
    client, _ = harness
    bad = {
        "nodes": [
            {"id": "a", "kind": "prompt", "output_key": "a", "config": {"prompt_template": "hi"}}
        ],
        "edges": [{"from": "a", "to": "ghost", "level": "normal"}],
    }
    assert client.patch("/api/ui/agent-profiles/alpha/graph", json=bad).status_code == 422


def test_graph_update_rejects_node_that_is_not_full_schema_valid(harness):
    client, _ = harness
    bad = {
        "nodes": [{"id": "a", "kind": "prompt", "output_key": "a", "config": {}}],
        "edges": [],
        "entry": "a",
    }
    assert client.patch("/api/ui/agent-profiles/alpha/graph", json=bad).status_code == 422


def test_graph_update_unknown_entry_returns_422(harness):
    client, _ = harness
    payload = {
        "nodes": [
            {"id": "a", "kind": "prompt", "output_key": "a", "config": {"prompt_template": "hi"}}
        ],
        "edges": [],
        "entry": "ghost",
    }
    assert client.patch("/api/ui/agent-profiles/alpha/graph", json=payload).status_code == 422


def test_graph_update_unknown_agent_returns_404(harness):
    client, _ = harness
    payload = {
        "nodes": [
            {"id": "a", "kind": "prompt", "output_key": "a", "config": {"prompt_template": "hi"}}
        ],
        "edges": [],
    }
    assert client.patch("/api/ui/agent-profiles/missing/graph", json=payload).status_code == 404


def _a2a_profile(agent_id: str = "a2a-example-agent") -> dict[str, Any]:
    return {
        "id": agent_id,
        "kind": "a2a",
        "name": "Example Agent",
        "group": "A2A agents",
        "status": "running",
        "version": "0.2.0",
        "transport": "JSONRPC",
        "url": "http://localhost:8080",
        "skills": [{"id": "health", "name": "Health"}],
        "tasks": [{"id": "t1", "status": "completed", "context_id": "ctx-1"}],
        "card": {"protocolVersion": "0.3.0"},
        "metrics": {"tasks": "1", "checkpoints": "1"},
        "graph": None,
    }


def test_list_includes_protocol_agents_from_registry(harness, monkeypatch):
    client, events = harness
    import src.backend.agents.routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [_a2a_profile()])
    response = client.get("/api/ui/agent-profiles")
    assert_acknowledged(response, ["agents.snapshot"])
    snapshot_events = [
        (event_type, payload)
        for event_type, payload, _ in events
        if event_type == "agents.snapshot"
    ]
    assert len(snapshot_events) == 1
    snapshot = snapshot_events[0][1]
    assert {agent["id"] for agent in snapshot["agents"]} == {"alpha", "beta", "a2a-example-agent"}


def test_detail_a2a_reads_through_registry(harness, monkeypatch):
    client, events = harness
    import src.backend.agents.routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [_a2a_profile()])
    response = client.get("/api/ui/agent-profiles/a2a-example-agent?request_id=r2")
    assert_acknowledged(
        response, ["agent.upserted", "agent.selected"], "test-middleware-request-id"
    )
    # Agent data is broadcast, not in the response
    upserted_event = next(p for t, p, _ in events if t == "agent.upserted")
    assert upserted_event["agent"]["status"] == "running"
    assert [t["id"] for t in upserted_event["agent"]["tasks"]] == ["t1"]
    assert upserted_event["agent"]["metrics"]["checkpoints"] == "1"
    assert client.get("/api/ui/agent-profiles/a2a-missing").status_code == 404


def test_graph_update_rejected_for_a2a(harness):
    client, _ = harness
    payload = {"nodes": [{"id": "a", "kind": "noop", "output_key": "a"}], "edges": []}
    assert (
        client.patch("/api/ui/agent-profiles/a2a-example-agent/graph", json=payload).status_code
        == 409
    )


def test_runs_route_publishes_run_summaries():
    """Two distinct routine-run checkpoint threads surface as two runs, newest first."""
    from src.backend.storage.documents import CheckpointThreadSnapshot

    doc_store = InMemoryDocumentStore()
    doc_store.save_document(
        PROGENA_STATE_PREFIX,
        "policy:default",
        ExecutionPolicy(id="default", name="Default").model_dump(mode="json"),
    )
    doc_store.upsert_checkpoint_thread_snapshot(
        CheckpointThreadSnapshot(
            thread_id="routine:alpha:rev-older:run-a",
            checkpoint_id="c1",
            checkpoint_ts="2026-01-01T00:00:00+00:00",
            messages=[],
        )
    )
    doc_store.upsert_checkpoint_thread_snapshot(
        CheckpointThreadSnapshot(
            thread_id="routine:alpha:rev-newer:run-b",
            checkpoint_id="c2",
            checkpoint_ts="2026-01-02T00:00:00+00:00",
            messages=[],
        )
    )
    # A checkpoint thread for a different routine must never leak into alpha's list.
    doc_store.upsert_checkpoint_thread_snapshot(
        CheckpointThreadSnapshot(
            thread_id="routine:beta:rev-x:run-c",
            checkpoint_id="c3",
            checkpoint_ts="2026-01-03T00:00:00+00:00",
            messages=[],
        )
    )
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: FakeAgentStore(),
            get_document_store=lambda: doc_store,
            broadcast_ui_event=broadcast,
        ),
        prefix="/api/ui",
    )
    client = TestClient(app)

    response = client.get("/api/ui/agent-profiles/alpha/runs")

    assert_acknowledged(response, ["agent.routine.runs.loaded"])
    payload = events[-1][1]
    assert payload["agent_id"] == "alpha"
    assert payload["before"] is None
    assert payload["has_more"] is False
    run_ids = [run["run_id"] for run in payload["runs"]]
    assert run_ids == ["run-b", "run-a"]  # newest last_checkpoint_at first
    newest = payload["runs"][0]
    assert newest["revision_id"] == "rev-newer"
    assert newest["thread_id"] == "routine:alpha:rev-newer:run-b"


def test_runs_route_pages_backward_with_before_cursor():
    """limit + before page backward through many runs without OFFSET drift."""
    from src.backend.storage.documents import CheckpointThreadSnapshot

    doc_store = InMemoryDocumentStore()
    doc_store.save_document(
        PROGENA_STATE_PREFIX,
        "policy:default",
        ExecutionPolicy(id="default", name="Default").model_dump(mode="json"),
    )
    for index in range(3):
        doc_store.upsert_checkpoint_thread_snapshot(
            CheckpointThreadSnapshot(
                thread_id=f"routine:alpha:rev-a:run-{index}",
                checkpoint_id=f"c{index}",
                checkpoint_ts=f"2026-01-0{index + 1}T00:00:00+00:00",
                messages=[],
            )
        )
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: FakeAgentStore(),
            get_document_store=lambda: doc_store,
            broadcast_ui_event=broadcast,
        ),
        prefix="/api/ui",
    )
    client = TestClient(app)

    first_page = client.get("/api/ui/agent-profiles/alpha/runs?limit=2")
    assert_acknowledged(first_page, ["agent.routine.runs.loaded"])
    first_payload = events[-1][1]
    assert [run["run_id"] for run in first_payload["runs"]] == ["run-2", "run-1"]
    assert first_payload["has_more"] is True

    oldest_of_first_page = first_payload["runs"][-1]["last_checkpoint_at"]
    second_page = client.get(
        "/api/ui/agent-profiles/alpha/runs", params={"limit": 2, "before": oldest_of_first_page}
    )
    assert_acknowledged(second_page, ["agent.routine.runs.loaded"])
    second_payload = events[-1][1]
    assert [run["run_id"] for run in second_payload["runs"]] == ["run-0"]
    assert second_payload["before"] == oldest_of_first_page
    assert second_payload["has_more"] is False


def test_revisions_route_publishes_revision_summaries():
    """Two revisions of one routine surface newest first, scoped to that routine."""
    doc_store = InMemoryDocumentStore()
    doc_store.save_document(
        PROGENA_STATE_PREFIX,
        "policy:default",
        ExecutionPolicy(id="default", name="Default").model_dump(mode="json"),
    )
    doc_store.upsert_routine_revision(
        "alpha", "rev-older", {"id": "alpha", "nodes": []}, captured_at="2026-01-01T00:00:00+00:00"
    )
    doc_store.upsert_routine_revision(
        "alpha",
        "rev-newer",
        {"id": "alpha", "nodes": [{"id": "n1"}]},
        captured_at="2026-01-02T00:00:00+00:00",
    )
    # A revision for a different routine must never leak into alpha's list.
    doc_store.upsert_routine_revision(
        "beta", "rev-x", {"id": "beta", "nodes": []}, captured_at="2026-01-03T00:00:00+00:00"
    )
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: FakeAgentStore(),
            get_document_store=lambda: doc_store,
            broadcast_ui_event=broadcast,
        ),
        prefix="/api/ui",
    )
    client = TestClient(app)

    response = client.get("/api/ui/agent-profiles/alpha/revisions")

    assert_acknowledged(response, ["agent.routine.revisions.loaded"])
    payload = events[-1][1]
    assert payload["agent_id"] == "alpha"
    revision_ids = [revision["revision_id"] for revision in payload["revisions"]]
    assert revision_ids == ["rev-newer", "rev-older"]  # newest captured_at first


def test_get_routine_revision_publishes_full_graph():
    doc_store = InMemoryDocumentStore()
    doc_store.save_document(
        PROGENA_STATE_PREFIX,
        "policy:default",
        ExecutionPolicy(id="default", name="Default").model_dump(mode="json"),
    )
    doc_store.upsert_routine_revision(
        "alpha",
        "rev-older",
        {"id": "alpha", "nodes": [{"id": "n1"}]},
        captured_at="2026-01-01T00:00:00+00:00",
    )
    events: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: FakeAgentStore(),
            get_document_store=lambda: doc_store,
            broadcast_ui_event=broadcast,
        ),
        prefix="/api/ui",
    )
    client = TestClient(app)

    response = client.get("/api/ui/agent-profiles/alpha/revisions/rev-older")

    assert_acknowledged(response, ["agent.routine.revision.loaded"])
    payload = events[-1][1]
    assert payload["agent_id"] == "alpha"
    assert payload["revision_id"] == "rev-older"
    assert payload["graph"] == {"id": "alpha", "nodes": [{"id": "n1"}]}

    missing = client.get("/api/ui/agent-profiles/alpha/revisions/does-not-exist")
    assert missing.status_code == 404


def test_run_state_no_database_url_returns_not_found_gracefully(harness):
    """InMemoryDocumentStore has no database_url -- no Postgres attempted,
    just an honest 'found: False' rather than a crash or fake data."""
    client, events = harness
    response = client.get(
        "/api/ui/agent-profiles/alpha/run-state",
        params={"thread_id": "routine:alpha:rev-a:some-session"},
    )
    assert_acknowledged(response, ["agent.routine.run_state.loaded"])
    payload = events[-1][1]
    assert payload == {
        "agent_id": "alpha",
        "thread_id": "routine:alpha:rev-a:some-session",
        "found": False,
        "run_meta": {},
        "data": {},
        "pending_approvals": [],
    }


def test_run_state_rejects_thread_id_from_a_different_routine(harness):
    client, _ = harness
    response = client.get(
        "/api/ui/agent-profiles/alpha/run-state",
        params={"thread_id": "routine:beta:rev-a:some-session"},
    )
    assert response.status_code == 400


def test_delete_publishes_correlated_deleted_event_after_persistence(harness):
    client, events = harness
    response = client.delete(
        "/api/ui/agent-profiles/alpha", headers={"X-Operation-Id": "routine-delete"}
    )
    assert_acknowledged(response, ["agent.deleted", "agents.snapshot"])
    assert events[0] == (
        "agent.deleted",
        {"agent_id": "alpha"},
        {"request_id": "routine-delete"},
    )
    assert events[1][0] == "agents.snapshot"
    assert events[1][2] == {"request_id": "routine-delete"}
    assert [agent["id"] for agent in events[1][1]["agents"]] == ["beta"]
    assert client.get("/api/ui/agent-profiles/alpha").status_code == 404
    assert client.delete("/api/ui/agent-profiles/alpha").status_code == 404
    assert len(events) == 2


def test_failed_delete_does_not_publish_events(harness, monkeypatch):
    client, events = harness

    def fail_delete(self, agent_id):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(FakeAgentStore, "delete_routine_agent", fail_delete)
    with pytest.raises(RuntimeError, match="delete failed"):
        client.delete("/api/ui/agent-profiles/alpha")
    assert events == []


def test_run_state_remains_readable_without_live_catalog_entry(harness):
    client, events = harness
    response = client.get(
        "/api/ui/agent-profiles/missing/run-state",
        params={"thread_id": "routine:missing:rev-a:some-session"},
    )
    assert_acknowledged(response, ["agent.routine.run_state.loaded"])
    assert events[-1][1]["found"] is False


def test_list_without_editable_store_publishes_registry_profiles(monkeypatch):
    events: list[tuple[str, dict[str, Any]]] = []
    import src.backend.agents.routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [_a2a_profile()])
    monkeypatch.setattr(routes, "acp_profiles_from_registry", lambda: [])

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: None,
            get_document_store=lambda: InMemoryDocumentStore(),
            broadcast_ui_event=lambda event_type, payload, **_kwargs: events.append(
                (event_type, payload)
            ),
        ),
        prefix="/api/ui",
    )

    response = TestClient(app).get("/api/ui/agent-profiles")
    assert_acknowledged(response, ["agents.snapshot"])
    assert len(events) == 1
    event_type, snapshot = events[0]
    assert event_type == "agents.snapshot"
    assert snapshot["total"] == 1
    assert snapshot["agents"][0]["id"] == "a2a-example-agent"


class TestBroadcastAgentProfilesSnapshot:
    """The standalone broadcast fn the registry-document watcher calls directly."""

    async def test_publishes_snapshot(self, monkeypatch):
        import src.backend.agents.routes as routes

        monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [])
        monkeypatch.setattr(routes, "acp_profiles_from_registry", lambda: [])

        store = FakeAgentStore()
        events: list[tuple[str, dict[str, Any]]] = []

        def broadcast(event_type, payload, **_kwargs):
            events.append((event_type, payload))
            return None

        response = await routes.broadcast_agent_profiles_snapshot(lambda: store, broadcast)

        assert response is not None
        assert response.total == 2
        assert events[-1][0] == "agents.snapshot"

    async def test_noop_when_store_unavailable(self):
        import src.backend.agents.routes as routes

        events: list[tuple[str, dict[str, Any]]] = []
        response = await routes.broadcast_agent_profiles_snapshot(
            lambda: None, lambda *a, **k: events.append((a, k))
        )

        assert response is None
        assert events == []


class TestValidateAgentGraph:
    def test_accepts_valid_routine(self):
        validate_agent_graph(
            [
                {"id": "parse", "kind": "prompt"},
                {"id": "gate", "kind": "eval"},
                {"id": "handoff", "kind": "routine"},
                {"id": "done", "kind": "noop"},
            ],
            [
                {"from": "parse", "to": "gate", "level": "normal"},
                {
                    "from": "gate",
                    "level": "route",
                    "router": "state_equals",
                    "state_key": "gate",
                    "branches": {"True": "handoff"},
                    "default": "done",
                },
                {"from": "handoff", "to": "done", "level": "normal"},
            ],
            "parse",
        )

    def test_rejects_duplicate_ids(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_agent_graph([{"id": "a", "kind": "noop"}, {"id": "a", "kind": "noop"}], [])

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="unknown node kind"):
            validate_agent_graph([{"id": "a", "kind": "bogus"}], [])

    def test_rejects_edge_to_unknown_node(self):
        with pytest.raises(ValueError, match="unknown target node"):
            validate_agent_graph(
                [{"id": "a", "kind": "noop"}],
                [{"from": "a", "to": "b", "level": "normal"}],
            )

    def test_rejects_missing_node_id(self):
        with pytest.raises(ValueError, match="non-empty string id"):
            validate_agent_graph([{"kind": "noop"}], [])

    def test_rejects_unknown_entry(self):
        with pytest.raises(ValueError, match="entry references unknown node"):
            validate_agent_graph([{"id": "a", "kind": "noop"}], [], "ghost")

    def test_router_rejects_branch_to_unknown_node(self):
        with pytest.raises(ValueError, match="unknown target node"):
            validate_agent_graph(
                [{"id": "a", "kind": "eval"}],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "ghost"},
                    }
                ],
            )

    def test_router_rejects_empty_branches(self):
        with pytest.raises(ValueError, match="at least one branch"):
            validate_agent_graph(
                [{"id": "a", "kind": "eval"}],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {},
                    }
                ],
            )

    def test_router_rejects_mixing_with_plain_edge(self):
        with pytest.raises(ValueError, match="more than one outgoing"):
            validate_agent_graph(
                [
                    {"id": "a", "kind": "eval"},
                    {"id": "b", "kind": "noop"},
                    {"id": "c", "kind": "noop"},
                ],
                [
                    {
                        "from": "a",
                        "level": "route",
                        "router": "state_equals",
                        "state_key": "a",
                        "branches": {"True": "b"},
                    },
                    {"from": "a", "to": "c", "level": "normal"},
                ],
            )


@pytest.mark.parametrize("answer", ["approve", "decline"])
def test_approval_queues_native_answer_and_rejects_duplicate(harness, monkeypatch, answer):
    from unittest.mock import AsyncMock

    import src.backend.workflows.events as events_module
    from src.backend.storage.documents import PROGENA_STATE_PREFIX
    from src.progena.routines.approvals import decision_key

    client, events = harness
    store = client.app.state.doc_store
    monkeypatch.setattr(
        events_module,
        "read_run_state",
        AsyncMock(
            return_value={
                "pending_approvals": [
                    {
                        "interrupt_id": "approval-id",
                        "node_id": "human",
                        "allowed_responses": ["approve", "decline"],
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(events_module.routine_events, "schedule", lambda _: None)
    body = {
        "thread_id": "routine:alpha:revision:session",
        "interrupt_id": "approval-id",
        "answer": answer,
    }
    response = client.post("/api/ui/agent-profiles/alpha/approval", json=body)
    assert response.status_code == 202
    queued = store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload
    assert len(queued) == 1
    assert queued[0]["metadata"]["operation"] == "resume"
    assert queued[0]["metadata"]["thread_id"] == body["thread_id"]
    assert queued[0]["metadata"]["answer"] == answer
    assert store.get_document(PROGENA_STATE_PREFIX, decision_key(body["thread_id"], "approval-id"))
    assert client.post("/api/ui/agent-profiles/alpha/approval", json=body).status_code == 409
    assert len(store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload) == 1


@pytest.mark.parametrize(
    "thread, interrupt, answer, status",
    [
        ("routine:beta:revision:session", "pending", "approve", 400),
        ("routine:alpha:revision:session", "stale", "approve", 409),
        ("routine:alpha:revision:session", "pending", "cancel", 409),
    ],
)
def test_invalid_approval_does_not_enqueue(harness, monkeypatch, thread, interrupt, answer, status):
    from unittest.mock import AsyncMock

    import src.backend.workflows.events as events_module
    from src.backend.storage.documents import PROGENA_STATE_PREFIX

    client, _ = harness
    monkeypatch.setattr(
        events_module,
        "read_run_state",
        AsyncMock(
            return_value={
                "pending_approvals": [
                    {
                        "interrupt_id": "pending",
                        "node_id": "human",
                        "allowed_responses": ["approve", "decline"],
                    }
                ]
            }
        ),
    )
    response = client.post(
        "/api/ui/agent-profiles/alpha/approval",
        json={"thread_id": thread, "interrupt_id": interrupt, "answer": answer},
    )
    assert response.status_code == status
    assert client.app.state.doc_store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None


def _runtime_gated_client(live: dict[str, bool]) -> tuple[TestClient, InMemoryDocumentStore]:
    doc_store = InMemoryDocumentStore()
    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: FakeAgentStore(),
            get_document_store=lambda: doc_store,
            broadcast_ui_event=lambda *_args, **_kwargs: None,
            is_runtime_live=lambda: live["value"],
        ),
        prefix="/api/ui",
    )
    return TestClient(app), doc_store


def test_run_and_approval_are_refused_while_the_runtime_is_down():
    """Nothing would claim the queued task, so it must not be accepted as 'Running'."""
    live = {"value": False}
    client, doc_store = _runtime_gated_client(live)
    approval = {
        "thread_id": "routine:alpha:revision:session",
        "interrupt_id": "i",
        "answer": "approve",
    }

    run = client.post("/api/ui/agent-profiles/alpha/run", json={})
    resume = client.post("/api/ui/agent-profiles/alpha/approval", json=approval)

    assert (run.status_code, resume.status_code) == (503, 503)
    assert "runtime is not running" in run.json()["detail"]
    assert doc_store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None

    live["value"] = True
    assert client.post("/api/ui/agent-profiles/alpha/run", json={}).status_code == 202
    assert len(doc_store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload) == 1


def test_run_control_rejects_runtime_conflict_without_terminal_event(harness, monkeypatch):
    import src.backend.agents.routes as routes
    from src.backend.integrations.runtime import RuntimeControlError

    client, events = harness

    class RuntimeClient:
        async def control_routine_run(self, *_args, **_kwargs):
            raise RuntimeControlError(409, "No active routine run owns this identity")

    monkeypatch.setattr(routes.lifecycle_state, "client", RuntimeClient())
    response = client.post(
        "/api/ui/agent-profiles/alpha/run-control",
        json={
            "thread_id": "routine:alpha:revision:session",
            "request_id": "run-attempt",
            "action": "cancel",
        },
    )

    assert response.status_code == 409
    assert events == []


def test_run_control_rejects_mismatched_runtime_identity_without_event(harness, monkeypatch):
    import src.backend.agents.routes as routes

    client, events = harness

    class RuntimeClient:
        async def control_routine_run(self, *_args, **_kwargs):
            return {
                "agent_id": "alpha",
                "thread_id": "routine:alpha:revision:other-session",
                "session_id": "other-session",
                "request_id": "run-attempt",
                "status": "stopped",
                "timestamp": "2026-10-26T00:00:00Z",
            }

    monkeypatch.setattr(routes.lifecycle_state, "client", RuntimeClient())
    response = client.post(
        "/api/ui/agent-profiles/alpha/run-control",
        json={
            "thread_id": "routine:alpha:revision:session",
            "request_id": "run-attempt",
            "action": "stop",
        },
    )

    assert response.status_code == 502
    assert events == []


def test_resume_requires_exact_stopped_checkpoint_and_retains_its_inputs(harness, monkeypatch):
    from unittest.mock import AsyncMock

    import src.backend.agents.routes as routes
    import src.backend.workflows.events as events_module

    client, events = harness
    store = client.app.state.doc_store
    session_id = "stopped-session"
    thread_id = f"routine:alpha:revision:{session_id}"
    store.save_document(
        PROGENA_STATE_PREFIX,
        routes.routine_run_control_key("alpha", session_id),
        {
            "agent_id": "alpha",
            "session_id": session_id,
            "thread_id": thread_id,
            "request_id": "stopped-attempt",
            "status": "stopped",
            "timestamp": "2026-10-26T00:00:00Z",
        },
    )
    checkpoint_reader = AsyncMock(return_value={"found": False})
    monkeypatch.setattr(events_module, "read_run_state", checkpoint_reader)
    body = {"session_id": session_id, "resume_thread_id": thread_id}

    missing = client.post(
        "/api/ui/agent-profiles/alpha/run",
        headers={"X-Operation-Id": "resume-attempt"},
        json=body,
    )
    assert missing.status_code == 409
    assert store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None

    checkpoint_reader.return_value = {"found": True}
    reused_attempt = client.post(
        "/api/ui/agent-profiles/alpha/run",
        headers={"X-Operation-Id": "stopped-attempt"},
        json=body,
    )
    assert reused_attempt.status_code == 409
    assert store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None

    wrong_thread = client.post(
        "/api/ui/agent-profiles/alpha/run",
        headers={"X-Operation-Id": "resume-attempt"},
        json={**body, "resume_thread_id": "routine:alpha:other-revision:stopped-session"},
    )
    assert wrong_thread.status_code == 409
    assert store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None

    monkeypatch.setattr(
        routes,
        "validate_against_schema",
        lambda *_args: pytest.fail("a verified resume must not validate live graph inputs"),
    )
    resumed = client.post(
        "/api/ui/agent-profiles/alpha/run",
        headers={"X-Operation-Id": "new-resume-attempt"},
        json=body,
    )

    assert_acknowledged(resumed, ["agent.routine.run.enqueued"], "new-resume-attempt")
    assert events[-1][0] == "agent.routine.run.enqueued"
    assert events[-1][1]["thread_id"] == thread_id
    checkpoint_reader.assert_awaited_with("alpha", thread_id, store)
    [task] = store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload
    assert task["metadata"]["session_id"] == session_id
    assert task["metadata"]["resume_thread_id"] == thread_id
    assert "parameters" not in task["metadata"]
    assert "target_node" not in task["metadata"]

    control_key = routes.routine_run_control_key("alpha", session_id)
    assert store.get_document(PROGENA_STATE_PREFIX, control_key).payload["status"] == "resuming"
    duplicate = client.post(
        "/api/ui/agent-profiles/alpha/run",
        headers={"X-Operation-Id": "second-resume-attempt"},
        json=body,
    )
    assert duplicate.status_code == 409
    assert len(store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload) == 1


def test_continue_settles_only_an_orphan_then_queues_its_checkpoint_resume(harness, monkeypatch):
    from unittest.mock import AsyncMock

    import src.backend.agents.routes as routes
    import src.backend.workflows.events as events_module

    client, events = harness
    store = client.app.state.doc_store
    session_id = "orphan-session"
    thread_id = f"routine:alpha:revision:{session_id}"
    outcome = {"status": "stopping"}

    class RuntimeClient:
        async def control_routine_run(self, agent_id, *, thread_id, request_id, action):
            assert action == "stop"
            lifecycle = {
                "agent_id": agent_id,
                "session_id": session_id,
                "thread_id": thread_id,
                "request_id": request_id,
                "status": outcome["status"],
                "timestamp": "2026-10-28T10:00:00Z",
            }
            if lifecycle["status"] == "stopped":
                # The runtime settles an orphan by persisting its stopped control.
                store.save_document(
                    PROGENA_STATE_PREFIX,
                    routes.routine_run_control_key(agent_id, session_id),
                    lifecycle,
                )
            return lifecycle

    monkeypatch.setattr(routes.lifecycle_state, "client", RuntimeClient())
    monkeypatch.setattr(events_module, "read_run_state", AsyncMock(return_value={"found": True}))
    body = {"thread_id": thread_id, "request_id": "orphan-attempt"}

    # A run its live owner is still executing is being stopped, not continued.
    live = client.post("/api/ui/agent-profiles/alpha/run-continue", json=body)
    assert live.status_code == 409
    assert store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None

    outcome["status"] = "stopped"
    resumed = client.post(
        "/api/ui/agent-profiles/alpha/run-continue",
        headers={"X-Operation-Id": "continue-attempt"},
        json=body,
    )

    assert_acknowledged(resumed, ["agent.routine.run.enqueued"], "continue-attempt")
    [task] = store.get_document(PROGENA_STATE_PREFIX, "background_tasks").payload
    assert task["metadata"]["resume_thread_id"] == thread_id
    assert "parameters" not in task["metadata"]
    assert events[-1][0] == "agent.routine.run.enqueued"


@pytest.mark.parametrize("queued", [False, True])
def test_resume_claim_is_released_only_when_nothing_was_queued(harness, monkeypatch, queued):
    from unittest.mock import AsyncMock

    import src.backend.agents.routes as routes
    import src.backend.workflows.events as events_module
    import src.backend.workflows.routine_runs as routine_runs

    client, _events = harness
    store = client.app.state.doc_store
    session_id = "stopped-session"
    thread_id = f"routine:alpha:revision:{session_id}"
    control_key = routes.routine_run_control_key("alpha", session_id)
    stopped = {
        "agent_id": "alpha",
        "session_id": session_id,
        "thread_id": thread_id,
        "request_id": "stopped-attempt",
        "status": "stopped",
        "timestamp": "2026-10-26T00:00:00Z",
    }
    store.save_document(PROGENA_STATE_PREFIX, control_key, stopped)
    monkeypatch.setattr(events_module, "read_run_state", AsyncMock(return_value={"found": True}))

    if queued:
        # The task is appended; only the later lifecycle write fails.
        save_document = store.save_document

        def failing_lifecycle_save(prefix, key, payload):
            if key.startswith(routine_runs.RUN_EVENT_PREFIX):
                raise RuntimeError("lifecycle write failed")
            return save_document(prefix, key, payload)

        monkeypatch.setattr(store, "save_document", failing_lifecycle_save)
    else:

        def failing_append(*_args, **_kwargs):
            raise RuntimeError("queue unavailable")

        monkeypatch.setattr(routine_runs, "append_background_task", failing_append)

    with pytest.raises(RuntimeError):
        client.post(
            "/api/ui/agent-profiles/alpha/run",
            headers={"X-Operation-Id": "resume-attempt"},
            json={"session_id": session_id, "resume_thread_id": thread_id},
        )

    control = store.get_document(PROGENA_STATE_PREFIX, control_key).payload
    if queued:
        assert control["status"] == "resuming"
        assert control["request_id"] == "resume-attempt"
    else:
        assert control == stopped


def test_cancelled_session_blocks_session_reuse_node_step_and_approval(harness):
    import src.backend.agents.routes as routes

    client, events = harness
    store = client.app.state.doc_store
    session_id = "cancelled-session"
    store.save_document(
        PROGENA_STATE_PREFIX,
        routes.routine_run_control_key("alpha", session_id),
        {
            "agent_id": "alpha",
            "session_id": session_id,
            "thread_id": f"routine:alpha:revision:{session_id}",
            "request_id": "cancelled-attempt",
            "status": "cancelled",
            "timestamp": "2026-10-26T00:00:00Z",
        },
    )

    reuse = client.post("/api/ui/agent-profiles/alpha/run", json={"session_id": session_id})
    node_step = client.post(
        "/api/ui/agent-profiles/alpha/run",
        json={"session_id": session_id, "target_node": "gate"},
    )
    approval = client.post(
        "/api/ui/agent-profiles/alpha/approval",
        json={
            "thread_id": f"routine:alpha:revision:{session_id}",
            "interrupt_id": "approval",
            "answer": "approve",
        },
    )

    assert (reuse.status_code, node_step.status_code, approval.status_code) == (409, 409, 409)
    assert store.get_document(PROGENA_STATE_PREFIX, "background_tasks") is None
    assert events == []
