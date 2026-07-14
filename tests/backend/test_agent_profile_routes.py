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

from app.backend.agent_profile_routes import (
    create_agent_profile_router,
    validate_agent_graph,
)

_DEFAULT_GRAPH = {
    "status": "Draft",
    "summary": None,
    "nodes": [
        {"id": "start", "label": "Start", "kind": "start", "x": 0, "y": 0},
        {"id": "ask", "label": "Ask", "kind": "ask", "prompt": "do the thing", "x": 200, "y": 0},
        {"id": "end", "label": "End", "kind": "end", "x": 400, "y": 0},
    ],
    "edges": [["start", "ask"], ["ask", "end"]],
}


def _agent(agent_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "id": agent_id,
        "kind": "subagent",
        "name": agent_id.title(),
        "handle": f"@{agent_id}",
        "group": "Subagents",
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
            "nodes": graph["nodes"],
            "edges": graph["edges"],
        }
        self._graphs[agent_id] = payload
        return payload


def _install_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or "test-middleware-request-id"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@pytest.fixture
def harness(monkeypatch):
    store = FakeAgentStore()
    events: list[tuple[str, dict[str, Any]]] = []

    def broadcast(event_type, payload, **kwargs):
        events.append((event_type, payload, kwargs))
        return None

    # The list/detail routes read A2A/ACP agents through from Progenitor's
    # registry; keep tests hermetic by returning no remote agent profiles by
    # default.
    import app.backend.agent_profile_routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [])
    monkeypatch.setattr(routes, "acp_profiles_from_registry", lambda: [])

    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(get_agent_store=lambda: store, broadcast_ui_event=broadcast),
        prefix="/api/ui",
    )
    return TestClient(app), events


def test_list_publishes_request_correlated_snapshot(harness):
    """The accepted client ID resolves on the canonical agents snapshot."""
    client, events = harness
    request_id = "b6ae12d0-8c70-4e3d-883d-89c89a1a2049"

    response = client.get(
        "/api/ui/agent-profiles",
        headers={"X-Request-ID": request_id},
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
        headers={"X-Request-ID": "req-1"},
    )
    assert_acknowledged(response, ["agent.upserted", "agent.selected"], "req-1")
    types = [e[0] for e in events]
    assert "agent.upserted" in types
    selected = next(kwargs for t, p, kwargs in events if t == "agent.selected")
    assert selected["request_id"] == "req-1"


def test_detail_unknown_returns_404(harness):
    client, _ = harness
    assert client.get("/api/ui/agent-profiles/missing").status_code == 404


def test_graph_schema_published(harness):
    """Graph schema endpoint was deleted - should return 404."""
    client, _ = harness
    assert client.get("/api/ui/agent-profiles/graph-schema").status_code == 404


def test_graph_update_persists_and_broadcasts(harness):
    client, events = harness
    payload = {
        "nodes": [{"id": "a", "kind": "start"}, {"id": "b", "kind": "end"}],
        "edges": [["a", "b", "done"]],
        "status": "Designed",
    }
    response = client.patch("/api/ui/agent-profiles/alpha/graph", json=payload)
    assert_acknowledged(response, ["agent.graph.updated"])
    assert events[-1][0] == "agent.graph.updated"
    # Broadcast payload contains updated graph data
    graph_payload = events[-1][1]
    assert graph_payload["graph"]["status"] == "Designed"
    # Persisted: subsequent detail broadcasts the new graph data
    detail_resp = client.get("/api/ui/agent-profiles/alpha")
    assert_acknowledged(detail_resp, ["agent.upserted", "agent.selected"])
    detail_upserted = next(p for t, p, _ in events if t == "agent.upserted")
    assert detail_upserted["agent"]["graph"]["status"] == "Designed"


def test_graph_update_invalid_returns_422(harness):
    client, _ = harness
    bad = {"nodes": [{"id": "a", "kind": "start"}], "edges": [["a", "ghost"]]}
    assert client.patch("/api/ui/agent-profiles/alpha/graph", json=bad).status_code == 422


def test_graph_update_unknown_agent_returns_404(harness):
    client, _ = harness
    payload = {"nodes": [{"id": "a", "kind": "start"}], "edges": []}
    assert client.patch("/api/ui/agent-profiles/missing/graph", json=payload).status_code == 404


def _a2a_profile(agent_id: str = "a2a-observe") -> dict[str, Any]:
    return {
        "id": agent_id,
        "kind": "a2a",
        "name": "Observe",
        "group": "A2A agents",
        "status": "running",
        "version": "0.2.0",
        "transport": "JSONRPC",
        "url": "http://localhost:8080",
        "skills": [{"id": "health", "name": "Health"}],
        "tasks": [
            {"id": "t1", "status": "completed", "context_id": "ctx-1", "title": "run health"}
        ],
        "card": {"protocolVersion": "0.3.0"},
        "metrics": {"tasks": "1", "checkpoints": "1"},
        "graph": None,
    }


def test_list_merges_subagents_and_read_through_a2a(harness, monkeypatch):
    client, events = harness
    import app.backend.agent_profile_routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [_a2a_profile()])
    response = client.get("/api/ui/agent-profiles")
    assert_acknowledged(response, ["agents.snapshot"])
    snapshot_events = [(t, p) for t, p, _ in events if t == "agents.snapshot"]
    assert len(snapshot_events) == 1
    snapshot = snapshot_events[0][1]
    assert {a["id"] for a in snapshot["agents"]} == {"alpha", "beta", "a2a-observe"}
    observe = next(a for a in snapshot["agents"] if a["id"] == "a2a-observe")
    assert observe["kind"] == "a2a"
    assert observe["graph"] is None
    assert observe["card"]["protocolVersion"] == "0.3.0"


def test_detail_a2a_reads_through_registry(harness, monkeypatch):
    client, events = harness
    import app.backend.agent_profile_routes as routes

    monkeypatch.setattr(routes, "a2a_profiles_from_registry", lambda: [_a2a_profile()])
    response = client.get("/api/ui/agent-profiles/a2a-observe?request_id=r2")
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
    payload = {"nodes": [{"id": "a", "kind": "input"}], "edges": []}
    assert client.patch("/api/ui/agent-profiles/a2a-observe/graph", json=payload).status_code == 409


def test_store_unavailable_returns_503():
    app = FastAPI()
    _install_request_id_middleware(app)
    app.include_router(
        create_agent_profile_router(
            get_agent_store=lambda: None, broadcast_ui_event=lambda *a, **k: None
        ),
        prefix="/api/ui",
    )
    assert TestClient(app).get("/api/ui/agent-profiles").status_code == 503


class TestBroadcastAgentProfilesSnapshot:
    """The standalone broadcast fn the registry-document watcher calls directly."""

    async def test_publishes_snapshot(self, monkeypatch):
        import app.backend.agent_profile_routes as routes

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
        import app.backend.agent_profile_routes as routes

        events: list[tuple[str, dict[str, Any]]] = []
        response = await routes.broadcast_agent_profiles_snapshot(
            lambda: None, lambda *a, **k: events.append((a, k))
        )

        assert response is None
        assert events == []


class TestValidateAgentGraph:
    def test_accepts_valid_series_of_asks(self):
        validate_agent_graph(
            [
                {"id": "start", "kind": "start"},
                {"id": "ask1", "kind": "ask", "prompt": "do x"},
                {"id": "tool1", "kind": "tool", "tool": "search"},
                {"id": "review", "kind": "decision", "prompt": "ok?"},
                {"id": "end", "kind": "end"},
            ],
            [
                ["start", "ask1"],
                ["ask1", "tool1"],
                ["tool1", "review"],
                ["review", "end", "approved"],
                ["review", "ask1", "revise"],
            ],
        )

    def test_rejects_duplicate_ids(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_agent_graph([{"id": "a"}, {"id": "a"}], [])

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="unknown node kind"):
            validate_agent_graph([{"id": "a", "kind": "bogus"}], [])

    def test_rejects_edge_to_unknown_node(self):
        with pytest.raises(ValueError, match="unknown node"):
            validate_agent_graph([{"id": "a"}], [["a", "b"]])

    def test_rejects_missing_node_id(self):
        with pytest.raises(ValueError, match="non-empty string id"):
            validate_agent_graph([{"kind": "start"}], [])

    def test_rejects_ask_without_prompt(self):
        with pytest.raises(ValueError, match="prompt"):
            validate_agent_graph([{"id": "a", "kind": "ask"}], [])

    def test_rejects_tool_without_tool(self):
        with pytest.raises(ValueError, match="tool"):
            validate_agent_graph([{"id": "a", "kind": "tool"}], [])

    def test_rejects_decision_edge_without_label(self):
        with pytest.raises(ValueError, match="label"):
            validate_agent_graph(
                [
                    {"id": "d", "kind": "decision", "prompt": "ok?"},
                    {"id": "e", "kind": "end"},
                ],
                [["d", "e"]],
            )

    def test_rejects_multiple_start_nodes(self):
        with pytest.raises(ValueError, match="one 'start'"):
            validate_agent_graph([{"id": "a", "kind": "start"}, {"id": "b", "kind": "start"}], [])
