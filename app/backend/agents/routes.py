"""Routes for the agent catalog consumed by the Agents page.

The catalog combines backend-owned routine metadata, routine graph documents
and revision/checkpoint history in the shared runtime store, manifest-backed
object agents, and live A2A/ACP profiles from the runtime registry.

Mirrors the chat router pattern: a GET acknowledges the request and publishes the
data on the shared ``/api/ui/stream`` so the frontend updates through its realtime
listener rather than the HTTP response body.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import yaml
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

import app.backend.http.contracts as http_contracts
from app.backend.agents.store import (
    AgentStore,
    a2a_profiles_from_registry,
    acp_profiles_from_registry,
)
from app.backend.http.requests import BroadcastUiEvent
from app.backend.http.requests import request_correlation_id as _request_correlation_id
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    store_database_url,
)
from app.backend.workflows.routine_runs import append_background_task, enqueue_routine_run
from app.progenitor.documents.catalog import load_schema
from app.progenitor.llm.structured import validate_against_schema
from app.progenitor.routines.graph import (
    ROUTINE_GRAPH_VERSION,
    validate_routine_graph,
    validate_routine_graph_payload,
)
from app.progenitor.utils import utcnow_isoformat


class AgentGraphModel(BaseModel):
    status: str = "Draft"
    summary: str | None = None
    entry: str | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    policy_id: str = "default"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    defs: dict[str, dict[str, Any]] | None = Field(default=None, alias="$defs")


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


class ObjectAgentCreateRequest(BaseModel):
    name: str
    description: str
    system_prompt: str
    version: str = "1.0.0"
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    permissions: list[dict[str, Any]] = Field(default_factory=list)
    interrupt_on: dict[str, bool | dict[str, Any]] = Field(default_factory=dict)


class ObjectAgentManifestUpdateRequest(BaseModel):
    """Raw ``SYSTEM.md`` text (YAML front matter + markdown body).

    Unlike ``ObjectAgentCreateRequest``, this is not shaped to a fixed set of
    known fields -- the editor page sends back the literal file content so an
    author can add, rename, or remove any front-matter key, not just the ones
    the runtime happens to interpret. ``AgentStore.update_object_agent``
    persists it verbatim and only rejects it if it fails to parse as a valid
    manifest (missing ``name``/``description``/``version``, bad YAML, etc.),
    or renames a ``native`` (repo-shipped) agent, which is not supported.
    """

    content: str


class AgentSkillUpdateRequest(BaseModel):
    """Raw ``SKILL.md`` text (front matter + body) for any skill, native or
    marketplace-installed. ``AgentStore.update_skill`` writes native skills
    to disk and marketplace skills to the document store; only a rename
    (front-matter ``name`` mismatch) is rejected.
    """

    content: str


class AgentGraphUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    entry: str | None = None
    status: str | None = None
    summary: str | None = None
    policy_id: str = "default"
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    defs: dict[str, dict[str, Any]] | None = Field(default=None, alias="$defs")


class RunRoutineRequest(BaseModel):
    # Both optional: omitted -> a normal, complete, one-shot run (what the
    # plain Run button sends). Present -> a step-to-node run against a
    # stable per-editor-session thread (see routine_run_node/subagent.py's
    # _run) -- session_id must stay identical across repeated calls within
    # one revision to resume rather than restart.
    session_id: str | None = None
    target_node: str | None = None
    # Values for the routine's declared input_schema.properties, e.g.
    # {"customer_id": "abc123"} -- validated against input_schema server-side
    # before the run is enqueued (see run_routine). Threaded through task
    # metadata -> routine_run_node -> the seed node's top-level invoke keys.
    parameters: dict[str, Any] | None = None


class RoutineSubscriptionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    thread_id: str | None = None
    session_id: str | None = None
    subscribed: bool = True


class RoutineApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    interrupt_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)


# JSON schema for a full routine graph document (id/name/description/version/entry/
# nodes/edges). Published so the Agents page can offer schema-aware JSON editing.
# The PATCH endpoint derives id/name/description/version from the owning agent
# record, combines those fields with the submitted graph, and validates the
# resulting full document before persistence.
AGENT_GRAPH_SCHEMA: dict[str, Any] = load_schema("graph.routine")


def validate_agent_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    entry: str | None = None,
) -> None:
    """Validate the nodes/edges slice used by graph-shape callers."""
    validate_routine_graph(nodes, edges)
    if entry is not None:
        node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
        if entry not in node_ids:
            raise ValueError(f"entry references unknown node: {entry}")


def validate_agent_graph_document(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    entry: str | None,
    *,
    agent_id: str,
    name: str,
    description: str,
    policy_id: str = "default",
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Validate the complete routine document that the route will persist."""
    document = {
        "id": agent_id,
        "name": name,
        "description": description,
        "version": ROUTINE_GRAPH_VERSION,
        "entry": entry,
        "nodes": nodes,
        "edges": edges,
        "policy_id": policy_id,
    }
    for key, value in (
        ("input_schema", input_schema),
        ("output_schema", output_schema),
        ("$defs", defs),
    ):
        if value is not None:
            document[key] = value
    validate_routine_graph_payload(document)


def build_agent_profiles(store: AgentStore | None) -> AgentProfilesResponse:
    """Build the internal-agent catalog from backend graph and manifest sources.

    Without an editable store only the runtime registry's A2A/ACP agents are listed.
    """
    # Metadata can briefly outlive its live graph while DELETE notifications
    # are reconciled. Reads must hide these rows without recreating documents.
    editable = (
        [
            record
            for record in store.list_agents()
            if record.get("kind") != "routine"
            or store.get_routine_document(record["id"]) is not None
        ]
        + store.list_object_agents()
        if store is not None
        else []
    )
    records = editable + a2a_profiles_from_registry() + acp_profiles_from_registry()
    agents = [AgentProfile(**record) for record in records]
    return AgentProfilesResponse(agents=agents, total=len(agents))


def object_agent_system_markdown(payload: ObjectAgentCreateRequest) -> str:
    """Render the portable manifest file stored for a user-created object agent."""
    manifest: dict[str, Any] = {
        "name": payload.name.strip(),
        "description": payload.description.strip(),
        "version": payload.version.strip(),
        "kind": "object",
    }
    for key, value in (
        ("model", payload.model.strip() if payload.model else None),
        ("tools", payload.tools),
        ("skills", payload.skills),
        ("permissions", payload.permissions),
        ("interrupt_on", payload.interrupt_on),
    ):
        if value:
            manifest[key] = value
    return (
        f"---\n{yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)}"
        f"---\n{payload.system_prompt.strip()}\n"
    )


async def broadcast_agent_profiles_snapshot(
    get_agent_store: Callable[[], AgentStore | None],
    broadcast_ui_event: BroadcastUiEvent,
    *,
    request_id: str | None = None,
) -> AgentProfilesResponse | None:
    """Rebuild and publish the agent-profiles snapshot on the UI stream.

    Shared by the ``GET /agent-profiles`` handler and the ``registry`` document
    change watcher (see ``monitoring.status._queue_state_watch_status``), so an agent
    editing ``/config/agents.json`` pushes live to the Agents page the same way
    a UI-driven edit already does. Returns ``None`` (no-op) if the agent store
    isn't configured yet rather than raising, since this also runs from a
    background watcher with no request to manage.
    """
    store = get_agent_store()
    if store is None:
        return None
    response = await asyncio.to_thread(build_agent_profiles, store)
    broadcast_ui_event("agents.snapshot", response.model_dump(by_alias=True), request_id=request_id)
    return response


def create_agent_profile_router(
    *,
    get_agent_store: Callable[[], AgentStore | None],
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    is_runtime_live: Callable[[], bool] | None = None,
) -> APIRouter:
    router = APIRouter()

    def _require_runtime() -> None:
        # Runs and approval answers only enqueue a task for the runtime to claim.
        # With no runtime consuming the queue they would sit as "Running…" forever,
        # so fail the request instead. ``None`` means no live signal is available.
        if is_runtime_live is not None and not is_runtime_live():
            raise HTTPException(
                status_code=503,
                detail="Progenitor runtime is not running; the routine cannot start",
            )

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
        if agent_id.startswith("object-"):
            return store.get_object_agent(agent_id)
        profile = store.get_agent(agent_id)
        if profile and profile.get("kind") == "routine" and profile.get("graph") is None:
            return None
        return profile

    @router.get(
        "/agent-profiles", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
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
        response = await asyncio.to_thread(build_agent_profiles, get_agent_store())
        broadcast_ui_event(
            "agents.snapshot", response.model_dump(by_alias=True), request_id=request_id
        )
        return http_contracts.acknowledgement_response(request_id, ["agents.snapshot"])

    @router.post(
        "/agent-profiles/object",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def create_object_agent_profile(
        payload: ObjectAgentCreateRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Persist an author-created object agent as a portable SYSTEM.md document."""
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        try:
            await asyncio.to_thread(
                store.save_user_object_agent,
                object_agent_system_markdown(payload),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = await asyncio.to_thread(build_agent_profiles, store)
        broadcast_ui_event(
            "agents.snapshot",
            response.model_dump(by_alias=True),
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(request_id, ["agents.snapshot"])

    @router.patch(
        "/agent-profiles/object/{agent_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def update_object_agent_profile(
        agent_id: str,
        payload: ObjectAgentManifestUpdateRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Update any manifest-backed object agent's SYSTEM.md manifest.

        Works for every source -- ``native`` agents are rewritten on disk
        under ``data/agents/objects/<name>/SYSTEM.md``, ``user`` and
        ``marketplace`` agents in the document store. The request carries the
        literal ``SYSTEM.md`` text, so any front-matter key can be added,
        renamed, or removed, not just the runtime-known ones.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        try:
            await asyncio.to_thread(
                store.update_object_agent,
                agent_id,
                payload.content,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = await asyncio.to_thread(build_agent_profiles, store)
        broadcast_ui_event(
            "agents.snapshot",
            response.model_dump(by_alias=True),
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(request_id, ["agents.snapshot"])

    @router.get(
        "/agent-profiles/skills",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_agent_skill_content(
        request: Request,
        path: str,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Publish one skill's ``SKILL.md`` body (front matter + prompt).

        ``path`` is the absolute ``/skills/...`` virtual-mount path stored in
        an object agent's manifest ``skills`` list. Resolves the same way the
        DeepAgent runtime's ``/skills`` mount does: a native skill
        (``data/skills/``) at ``/skills/<name>`` or an installed
        marketplace-plugin skill at ``/skills/<marketplace>__<plugin>/<name>``.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        skill = await asyncio.to_thread(store.resolve_skill, path)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"skill {path!r} not found")
        broadcast_ui_event("agent.skill.content.updated", skill, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["agent.skill.content.updated"])

    @router.patch(
        "/agent-profiles/skills",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def update_agent_skill_content(
        payload: AgentSkillUpdateRequest,
        request: Request,
        path: str,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Update one skill's ``SKILL.md`` text.

        Both native (rewritten on disk) and marketplace-installed (rewritten
        in the document store) skills are editable; only renaming a skill
        (changing its front-matter ``name``) is refused, since the mount
        path is referenced by agent manifests' ``skills:`` lists.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        try:
            skill = await asyncio.to_thread(store.update_skill, path, payload.content)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        broadcast_ui_event("agent.skill.content.updated", skill, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["agent.skill.content.updated"])

    @router.get(
        "/agent-profiles/{agent_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
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
        broadcast_ui_event(
            "agent.upserted", {"agent": profile.model_dump(by_alias=True)}, request_id=request_id
        )
        broadcast_ui_event(
            "agent.selected",
            {"agent": profile.model_dump(by_alias=True)},
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, ["agent.upserted", "agent.selected"]
        )

    @router.patch(
        "/agent-profiles/{agent_id}/graph",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
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
        agent = await asyncio.to_thread(store.get_agent, agent_id)
        if agent is None or (agent.get("kind") == "routine" and agent.get("graph") is None):
            raise HTTPException(status_code=404, detail="Agent not found")
        try:
            validate_agent_graph_document(
                body.nodes,
                body.edges,
                body.entry,
                agent_id=agent_id,
                name=str(agent.get("name") or agent_id),
                description=str(agent.get("summary") or ""),
                policy_id=body.policy_id,
                input_schema=body.input_schema,
                output_schema=body.output_schema,
                defs=body.defs,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        policy_document = await asyncio.to_thread(
            get_document_store().get_document,
            PROGENITOR_STATE_PREFIX,
            f"policy:{body.policy_id}",
            None,
        )
        if policy_document is None:
            raise HTTPException(
                status_code=422, detail=f"Unknown execution policy '{body.policy_id}'"
            )

        updated = store.update_graph(
            agent_id,
            {
                "entry": body.entry,
                "nodes": body.nodes,
                "edges": body.edges,
                "policy_id": body.policy_id,
                "input_schema": body.input_schema,
                "output_schema": body.output_schema,
                "$defs": body.defs,
            },
            status=body.status,
            summary=body.summary,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        graph = AgentGraphModel(**updated)
        broadcast_ui_event(
            "agent.graph.updated",
            {"agent_id": agent_id, "graph": graph.model_dump(by_alias=True)},
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(request_id, ["agent.graph.updated"])

    @router.put(
        "/agent-profiles/{agent_id}/run-subscription",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def subscribe_routine_runs(
        agent_id: str,
        body: RoutineSubscriptionRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        from app.backend.workflows.events import routine_events

        request_id = _request_correlation_id(request, x_request_id)
        if body.subscribed:
            if body.client_id not in routine_events.connections:
                raise HTTPException(status_code=409, detail="UI event stream is not connected")
            if body.thread_id and not body.thread_id.startswith(f"routine:{agent_id}:"):
                raise HTTPException(
                    status_code=400, detail="thread_id does not belong to this routine"
                )
            if await asyncio.to_thread(_require_store().get_agent, agent_id) is None:
                raise HTTPException(status_code=404, detail="Agent not found")
            routine_events.interests[body.client_id] = {
                "agent_id": agent_id,
                "thread_id": body.thread_id,
                "session_id": body.session_id,
            }
        elif routine_events.interests.get(body.client_id, {}).get("agent_id") == agent_id:
            routine_events.interests.pop(body.client_id, None)
        broadcast_ui_event(
            "agent.routine.subscription.updated",
            {
                "agent_id": agent_id,
                "subscribed": body.subscribed,
            },
            request_id=request_id,
            client_id=body.client_id,
        )
        if body.subscribed:
            routine_events.schedule(agent_id)
        return http_contracts.acknowledgement_response(
            request_id, ["agent.routine.subscription.updated"]
        )

    @router.post(
        "/agent-profiles/{agent_id}/run",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def run_routine(
        agent_id: str,
        request: Request,
        body: RunRoutineRequest = RunRoutineRequest(),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Enqueue a real, deterministic execution of a routine graph.

        Writes a background-task entry (``metadata.kind = "routine_run"``)
        onto the same Postgres-backed queue the heartbeat runtime already
        drains (``app.progenitor.orchestration.background`` -- both processes
        share the LangGraph ``store`` table via the same
        ``("progenitor", "state")`` namespace / ``"progenitor.state"``
        prefix). The heartbeat graph recognizes that marker and routes
        straight to ``routine_run_node``, which calls the routine's already
        -compiled subagent runnable directly -- no LLM turn decides whether
        to run it, unlike the old client-side mock this replaces.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        agent = await asyncio.to_thread(store.get_agent, agent_id)
        if agent is None or (agent.get("kind") == "routine" and agent.get("graph") is None):
            raise HTTPException(status_code=404, detail="Agent not found")

        input_schema = (agent.get("graph") or {}).get("input_schema")
        if input_schema:
            validation_error = validate_against_schema(body.parameters or {}, input_schema)
            if validation_error is not None:
                raise HTTPException(
                    status_code=422,
                    detail=f"Run parameters failed validation: {validation_error}",
                )

        _require_runtime()
        lifecycle = enqueue_routine_run(
            get_document_store(),
            agent_id,
            request_id=request_id,
            session_id=body.session_id,
            parameters=body.parameters,
            target_node=body.target_node,
        )
        from app.backend.workflows.events import routine_events

        if routine_events.claim_lifecycle(lifecycle):
            broadcast_ui_event("agent.routine.run.enqueued", lifecycle, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["agent.routine.run.enqueued"])

    @router.post(
        "/agent-profiles/{agent_id}/approval",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def answer_routine_approval(
        agent_id: str,
        body: RoutineApprovalRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        from app.backend.workflows.events import read_run_state, routine_events
        from app.progenitor.routines.approvals import approval_lock, decision_key, validate_answer
        from app.progenitor.routines.events import RUN_EVENT_PREFIX

        if (
            not body.thread_id.startswith(f"routine:{agent_id}:")
            or len(body.thread_id.split(":")) != 4
        ):
            raise HTTPException(status_code=400, detail="thread_id does not belong to this routine")
        _require_runtime()
        request_id = _request_correlation_id(request, x_request_id)
        doc_store = get_document_store()
        try:
            async with approval_lock(body.thread_id, store_database_url(doc_store)):
                state = await read_run_state(agent_id, body.thread_id, doc_store)
                approval = validate_answer(
                    state["pending_approvals"], body.answer, body.interrupt_id
                )
                claim_key = decision_key(body.thread_id, body.interrupt_id)
                if doc_store.get_document(PROGENITOR_STATE_PREFIX, claim_key) is not None:
                    raise ValueError("An answer for this approval has already been submitted")
                metadata = {
                    "kind": "routine_run",
                    "operation": "resume",
                    "routine_id": agent_id,
                    "thread_id": body.thread_id,
                    "session_id": body.thread_id.split(":")[-1],
                    "interrupt_id": approval["interrupt_id"],
                    "answer": body.answer,
                    "request_id": request_id,
                }
                queued_at = utcnow_isoformat()
                doc_store.save_document(PROGENITOR_STATE_PREFIX, claim_key, metadata)
                try:
                    append_background_task(
                        doc_store,
                        {
                            "id": f"routine-answer-{uuid.uuid4().hex}",
                            "content": "Answer a routine approval.",
                            "metadata": metadata,
                            "created_at": queued_at,
                        },
                    )
                except Exception:
                    doc_store.delete_document(PROGENITOR_STATE_PREFIX, claim_key)
                    raise
                lifecycle = {
                    "agent_id": agent_id,
                    "thread_id": body.thread_id,
                    "session_id": metadata["session_id"],
                    "request_id": request_id,
                    "status": "enqueued",
                    "timestamp": queued_at,
                }
                doc_store.save_document(
                    PROGENITOR_STATE_PREFIX, f"{RUN_EVENT_PREFIX}{request_id}:enqueued", lifecycle
                )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if routine_events.claim_lifecycle(lifecycle):
            broadcast_ui_event("agent.routine.run.enqueued", lifecycle, request_id=request_id)
        routine_events.schedule(agent_id)
        return http_contracts.acknowledgement_response(request_id, ["agent.routine.run.enqueued"])

    @router.get(
        "/agent-profiles/{agent_id}/run-state",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_routine_run_state(
        agent_id: str,
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Read a routine run's live per-node timing/token/output state.

        Reads the given thread_id's latest checkpoint directly -- the same
        Postgres-backed LangGraph checkpointer the runtime writes to, opened
        short-lived here (mirrors PostgresRoutineRevisionReader's "rare
        operation, no persistent connection" shape) since the backend
        process holds no long-lived checkpointer of its own. Returns
        run_meta (per-node duration, see factory.py's
        _instrument_node_callable) and data (each node's output_key value)
        as of that checkpoint.
        """
        request_id = _request_correlation_id(request, x_request_id)
        if not thread_id.startswith(f"routine:{agent_id}:"):
            raise HTTPException(status_code=400, detail="thread_id does not belong to this routine")

        from app.backend.workflows.events import read_run_state

        payload = await read_run_state(agent_id, thread_id, get_document_store())

        broadcast_ui_event(
            "agent.routine.run_state.loaded",
            payload,
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, ["agent.routine.run_state.loaded"]
        )

    @router.get(
        "/agent-profiles/{agent_id}/runs",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def list_routine_runs(
        agent_id: str,
        request: Request,
        before: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Publish one page of recent execution runs for one routine.

        A run is a distinct checkpoint thread named
        ``routine:<agent_id>:<revision_id>:<run_id>`` (see
        ``app.progenitor.routines.subagent.create_routine_subagent``); this
        reads the ``checkpoints`` table grouped by that thread_id rather than
        any bespoke run-history storage.

        ``before`` pages backward from a cursor (a ``last_checkpoint_at``
        value from a previously returned run) for "Load more" in the Runs
        panel; omitted, this returns the newest page. ``has_more`` in the
        response tells the caller whether another page exists.
        """
        request_id = _request_correlation_id(request, x_request_id)
        runs, has_more = await asyncio.to_thread(
            get_document_store().list_routine_runs, agent_id, limit=limit, before=before
        )
        payload = {
            "agent_id": agent_id,
            "before": before,
            "has_more": has_more,
            "runs": [
                {
                    "thread_id": run.thread_id,
                    "revision_id": run.revision_id,
                    "run_id": run.run_id,
                    "started_at": run.started_at,
                    "last_checkpoint_at": run.last_checkpoint_at,
                    "checkpoint_count": run.checkpoint_count,
                    "failed": run.failed,
                }
                for run in runs
            ],
        }
        broadcast_ui_event("agent.routine.runs.loaded", payload, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["agent.routine.runs.loaded"])

    @router.get(
        "/agent-profiles/{agent_id}/revisions",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def list_routine_revisions(
        agent_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Publish every captured revision of one routine's graph document.

        Reads ``routine_graph_revisions`` (a Postgres trigger appends one
        immutable row there on every write to the routine's live document);
        newest first.
        """
        request_id = _request_correlation_id(request, x_request_id)
        revisions = await asyncio.to_thread(get_document_store().list_routine_revisions, agent_id)
        payload = {
            "agent_id": agent_id,
            "revisions": [
                {"revision_id": revision.revision_id, "captured_at": revision.captured_at}
                for revision in revisions
            ],
        }
        broadcast_ui_event("agent.routine.revisions.loaded", payload, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, ["agent.routine.revisions.loaded"]
        )

    @router.get(
        "/agent-profiles/{agent_id}/revisions/{revision_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_routine_revision(
        agent_id: str,
        revision_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Publish one revision's full graph document, for the Revisions panel to render."""
        request_id = _request_correlation_id(request, x_request_id)
        graph = await asyncio.to_thread(
            get_document_store().get_routine_revision, agent_id, revision_id
        )
        if graph is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        broadcast_ui_event(
            "agent.routine.revision.loaded",
            {"agent_id": agent_id, "revision_id": revision_id, "graph": graph},
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, ["agent.routine.revision.loaded"]
        )

    @router.delete(
        "/agent-profiles/{agent_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_agent_profile(
        agent_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Delete a routine or an author-created ("source: user") object agent.

        Native object manifests are Postgres documents seeded once from the
        repo; UI policy protects them from deletion here. Marketplace agents
        are removed through Plugins > Uninstall. A2A/ACP agents belong to
        external registries and have no local catalog row to delete.
        """
        request_id = _request_correlation_id(request, x_request_id)
        store = _require_store()
        if agent_id.startswith("object-"):
            try:
                deleted = await asyncio.to_thread(store.delete_user_object_agent, agent_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if not deleted:
                raise HTTPException(status_code=404, detail="Object agent not found")
        else:
            agent = await asyncio.to_thread(store.get_agent, agent_id)
            if agent is None or agent.get("kind") != "routine":
                raise HTTPException(status_code=404, detail="Agent not found")
            await asyncio.to_thread(store.delete_routine_agent, agent_id)
        broadcast_ui_event("agent.deleted", {"agent_id": agent_id}, request_id=request_id)
        response = await asyncio.to_thread(build_agent_profiles, store)
        broadcast_ui_event(
            "agents.snapshot",
            response.model_dump(by_alias=True),
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, ["agent.deleted", "agents.snapshot"]
        )

    return router
