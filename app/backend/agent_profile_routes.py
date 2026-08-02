"""Agent profile routes for the backend-owned agent catalog.

This is a distinct resource from the runtime A2A registry which describes live
runtime state; these routes serve the editable agent catalog (definition +
LangGraph-style graph) stored in the backend database and consumed by the
Agents page.

Mirrors the chat router pattern: a GET acknowledges the request and publishes the
data on the shared ``/api/ui/stream`` so the frontend updates through its realtime
listener rather than the HTTP response body.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.progenitor.routines.graph import VALID_ROUTINE_NODE_KINDS, validate_routine_graph
from app.progenitor.schemas import load_schema

from .agent_store import AgentStore, a2a_profiles_from_registry, acp_profiles_from_registry
from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .request_utils import BroadcastUiEvent
from .request_utils import request_correlation_id as _request_correlation_id


class AgentGraphModel(BaseModel):
    status: str = "Draft"
    summary: str | None = None
    entry: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


class AgentProfile(BaseModel):
    id: str
    kind: str
    name: str
    handle: str | None = None
    group: str | None = None
    color: str | None = None
    model: str | None = None
    role: str | None = None
    version: str | None = None
    summary: str | None = None
    status: str | None = None
    enabled: bool = True
    prompt: str = ""
    skills: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    repositories: list[dict[str, Any]] = []
    knowledge: list[dict[str, Any]] = []
    memories: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}
    transport: str | None = None
    url: str | None = None
    card: dict[str, Any] = {}
    updated: str | None = None
    graph: AgentGraphModel | None = None


class AgentProfilesResponse(BaseModel):
    agents: list[AgentProfile]
    total: int


class AgentGraphUpdateRequest(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    entry: str | None = None
    status: str | None = None
    summary: str | None = None


# Allowed node "kind" values for the editable routine graph. These are executable
# step kinds, compiled into a LangGraph runnable by
# ``app.progenitor.routines.factory``. Re-exported here for callers that imported
# the old name (e.g. ``agent_store.reset_legacy_graphs``'s dev migration helper).
VALID_NODE_KINDS = VALID_ROUTINE_NODE_KINDS

# JSON schema for a full routine graph document (id/name/description/version/entry/
# nodes/edges). Published so the Agents page can offer schema-aware JSON editing.
# The PATCH endpoint below only validates the nodes/edges/entry slice it receives
# (via ``validate_agent_graph``), since id/name/description/version are derived
# from the owning agent record rather than round-tripped on the wire.
AGENT_GRAPH_SCHEMA: dict[str, Any] = load_schema("graph.routine")


def validate_agent_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], entry: str | None = None
) -> None:
    """Validate a routine graph payload structurally. Raises ValueError on bad input."""
    validate_routine_graph(nodes, edges)
    if entry is not None:
        node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
        if entry not in node_ids:
            raise ValueError(f"entry references unknown node: {entry}")


def build_agent_profiles(store: AgentStore) -> AgentProfilesResponse:
    """Backend-owned subagents (editable) + A2A and ACP agents read through from
    Progenitor's registry document (live status + card, no backend rows)."""
    records = (
        list(store.list_agents()) + a2a_profiles_from_registry() + acp_profiles_from_registry()
    )
    agents = [AgentProfile(**record) for record in records]
    return AgentProfilesResponse(agents=agents, total=len(agents))


async def broadcast_agent_profiles_snapshot(
    get_agent_store: Callable[[], AgentStore | None],
    broadcast_ui_event: BroadcastUiEvent,
    *,
    request_id: str | None = None,
) -> AgentProfilesResponse | None:
    """Rebuild and publish the agent-profiles snapshot on the UI stream.

    Shared by the ``GET /agent-profiles`` handler and the ``registry`` document
    change watcher (see ``ui_routes._queue_state_watch_status``), so an agent
    editing ``/config/agents.json`` pushes live to the Agents page the same way
    a UI-driven edit already does. Returns ``None`` (no-op) if the agent store
    isn't configured yet rather than raising, since this also runs from a
    background watcher with no request to manage.
    """
    store = get_agent_store()
    if store is None:
        return None
    response = await asyncio.to_thread(build_agent_profiles, store)
    broadcast_ui_event("agents.snapshot", response.model_dump(), request_id=request_id)
    return response


def create_agent_profile_router(
    *,
    get_agent_store: Callable[[], AgentStore | None],
    broadcast_ui_event: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    def _require_store() -> AgentStore:
        store = get_agent_store()
        if store is None:
            raise HTTPException(status_code=503, detail="Agent store is not configured")
        return store

    def _resolve_profile(store: AgentStore, agent_id: str) -> dict[str, Any] | None:
        if agent_id.startswith("a2a-"):
            for profile in a2a_profiles_from_registry():
                if profile["id"] == agent_id:
                    return profile
            return None
        if agent_id.startswith("acp-"):
            for profile in acp_profiles_from_registry():
                if profile["id"] == agent_id:
                    return profile
            return None
        return store.get_agent(agent_id)

    @router.get("/agent-profiles", status_code=202, response_model=UiRequestAcknowledgement)
    async def list_agent_profiles(
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge agent-list requests and publish the list on the UI stream.

        Subagents come from the backend DB; A2A agents are read through from
        Progenitor's registry document so they always reflect the runtime's live
        view (status + agent card) without the backend fetching cards itself.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        response = await asyncio.to_thread(build_agent_profiles, store)
        broadcast_ui_event("agents.snapshot", response.model_dump(), request_id=request_id)
        return acknowledgement_response(request_id, ["agents.snapshot"])

    @router.get(
        "/agent-profiles/{agent_id}", status_code=202, response_model=UiRequestAcknowledgement
    )
    async def get_agent_profile(
        agent_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge agent-detail requests and publish the detail on the UI stream."""
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        agent = await asyncio.to_thread(_resolve_profile, store, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        profile = AgentProfile(**agent)
        broadcast_ui_event("agent.upserted", {"agent": profile.model_dump()}, request_id=request_id)
        broadcast_ui_event(
            "agent.selected",
            {"agent": profile.model_dump()},
            request_id=request_id,
        )
        return acknowledgement_response(request_id, ["agent.upserted", "agent.selected"])

    @router.patch(
        "/agent-profiles/{agent_id}/graph", status_code=202, response_model=UiRequestAcknowledgement
    )
    async def update_agent_graph(
        agent_id: str,
        body: AgentGraphUpdateRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Validate and persist an edited agent graph, then broadcast the update."""
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        if agent_id.startswith("a2a-") or agent_id.startswith("acp-"):
            raise HTTPException(status_code=409, detail="A2A and ACP agents are read-only")
        try:
            validate_agent_graph(body.nodes, body.edges, body.entry)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        updated = store.update_graph(
            agent_id,
            {"entry": body.entry, "nodes": body.nodes, "edges": body.edges},
            status=body.status,
            summary=body.summary,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        graph = AgentGraphModel(**updated)
        broadcast_ui_event(
            "agent.graph.updated",
            {"agent_id": agent_id, "graph": graph.model_dump()},
            request_id=request_id,
        )
        return acknowledgement_response(request_id, ["agent.graph.updated"])

    return router
