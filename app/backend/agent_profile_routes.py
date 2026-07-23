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

from .agent_store import AgentStore, a2a_profiles_from_registry, acp_profiles_from_registry
from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .request_utils import BroadcastUiEvent
from .request_utils import request_correlation_id as _request_correlation_id


class AgentGraphModel(BaseModel):
    status: str = "Draft"
    summary: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[list[Any]] = []


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
    edges: list[list[Any]]
    status: str | None = None
    summary: str | None = None


# Allowed node "kind" values for the editable subagent workflow graph. These are
# executable step kinds (not decorative): a workflow is a series of ``ask`` steps
# with optional ``tool`` calls and ``decision`` branches between a ``start`` and
# an ``end``. Kept in sync with the frontend node renderer; the JSON-schema
# endpoint publishes these.
VALID_NODE_KINDS = {"start", "ask", "tool", "decision", "end"}


# Predefined JSON schema for an editable subagent workflow graph. Published so the
# Agents page can offer schema-aware JSON editing; the PATCH endpoint enforces the
# same structural rules via ``validate_agent_graph``.
AGENT_GRAPH_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AgentGraph",
    "type": "object",
    "required": ["nodes", "edges"],
    "description": (
        "A subagent workflow: a series of steps run between a 'start' and an 'end'. "
        "'ask' steps carry a prompt; 'tool' steps name a tool; 'decision' steps "
        "branch via labelled outgoing edges."
    ),
    "properties": {
        "status": {"type": "string"},
        "summary": {"type": ["string", "null"]},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "label": {"type": "string"},
                    "kind": {"type": "string", "enum": sorted(VALID_NODE_KINDS)},
                    "prompt": {
                        "type": "string",
                        "description": "Instruction for an 'ask' step (required for kind=ask).",
                    },
                    "tool": {
                        "type": "string",
                        "description": "Tool name for a 'tool' step (required for kind=tool).",
                    },
                    "detail": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 3,
                "items": {"type": "string"},
                "description": (
                    "[source, target] with an optional label; edges leaving a "
                    "'decision' node must carry a label naming the branch."
                ),
            },
        },
    },
}


def validate_agent_graph(nodes: list[dict[str, Any]], edges: list[list[Any]]) -> None:
    """Validate a workflow graph payload structurally. Raises ValueError on bad input."""
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("graph nodes and edges must be lists")

    ids: set[str] = set()
    kinds: dict[str, str | None] = {}
    start_count = 0
    end_count = 0
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("each node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("each node needs a non-empty string id")
        if node_id in ids:
            raise ValueError(f"duplicate node id: {node_id}")
        ids.add(node_id)
        kind = node.get("kind")
        if kind is not None and kind not in VALID_NODE_KINDS:
            raise ValueError(f"unknown node kind: {kind}")
        kinds[node_id] = kind
        if kind == "start":
            start_count += 1
        elif kind == "end":
            end_count += 1
        elif kind == "ask" and not _nonempty_str(node.get("prompt")):
            raise ValueError(f"'ask' node needs a non-empty prompt: {node_id}")
        elif kind == "tool" and not _nonempty_str(node.get("tool")):
            raise ValueError(f"'tool' node needs a non-empty tool: {node_id}")

    if start_count > 1:
        raise ValueError("graph can have at most one 'start' node")
    if end_count > 1:
        raise ValueError("graph can have at most one 'end' node")

    for edge in edges:
        if not isinstance(edge, list) or len(edge) < 2:
            raise ValueError("each edge must be a [source, target] (optional label) list")
        source, target = edge[0], edge[1]
        if source not in ids or target not in ids:
            raise ValueError(f"edge references unknown node: {source} -> {target}")
        if kinds.get(source) == "decision" and not _nonempty_str(
            edge[2] if len(edge) > 2 else None
        ):
            raise ValueError(f"edges leaving a 'decision' node need a label: {source} -> {target}")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


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
    editing ``/state/agents.json`` pushes live to the Agents page the same way
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
            validate_agent_graph(body.nodes, body.edges)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        updated = store.update_graph(
            agent_id,
            {"nodes": body.nodes, "edges": body.edges},
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
