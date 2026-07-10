"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.

Reads real state data from file-backed runtime artifacts plus shared backend-managed state:
- health: system status (cycle_count, idle state, queue stats), persisted in the
  shared Postgres document store; liveness itself comes from a live IPC heartbeat
  check, not a stored document (see main.get_progenitor_liveness())
- agents.json + the shared MCP state document: Registered agents and enabled MCP servers
- build_state.json: Agent builds ONLY (inner loop, LLM messages, verification)
- current_state.json: Progenitor agent state (chat history, tool calls) - user session
- schedules: scheduled tasks, persisted in the shared Postgres document store
  (system feature, persists beyond user sessions)
"""

import asyncio
import contextlib
import csv
import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Literal, cast

import httpx
from croniter import croniter
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from app.progenitor.schedule import Scheduler, default_timezone_name
from app.progenitor.schedule.service import generate_schedule_id
from app.progenitor.utils import utcnow
from app.progenitor.workflows import (
    WORKFLOW_ALLOWED_EVENT_TYPES,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
    WorkflowDefinition,
    WorkflowListItem,
    WorkflowNodeCode,
    WorkflowNodeUser,
    WorkflowRun,
    default_workflow_definition,
    workflow_id_from_path,
)

from .agent_profile_routes import broadcast_agent_profiles_snapshot, create_agent_profile_router
from .agent_store import get_agent_store
from .alerts import (
    AlertInfo,
    AlertsResponse,
    MarkAlertsViewedRequest,
    MarkAlertsViewedResponse,
    alert_summary,
    get_alert,
    get_alerts_response,
    mark_alerts_viewed,
)
from .alerts import (
    create_alert as store_alert,
)
from .chat_routes import broadcast_chat_sessions_snapshot, create_chat_router
from .chat_sessions import UiEventEnvelope
from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store
from .inbox_routes import (
    INBOX_MESSAGE_KEY_PREFIX,
    broadcast_inbox_snapshot,
    create_inbox_router,
)
from .knowledge_service import snapshot_bases as snapshot_knowledge_bases
from .prompt_service import PromptService
from .skill_service import SkillService
from .state_reader import StateReader, get_state_reader
from .threads import (
    ThreadsResponse,
    complete_thread,
    fail_thread,
    list_threads,
    upsert_thread,
)
from .workflow_service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    build_user_node_instruction,
    delete_workflow_definition,
    find_active_workflow_run,
    get_workflow_definition,
    get_workflow_run,
    list_workflow_list_items,
    list_workflow_runs,
    save_workflow_definition,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])

KANBAN_EVENT_TYPE_LABELS = {
    "kanban.card.created": "Card created",
    "kanban.card.updated": "Card updated",
    "kanban.card.moved": "Card moved",
    "kanban.card.deleted": "Card deleted",
    "kanban.card.status_changed": "Status changed",
}
MAX_EVENT_WIDGET_LOGS = 20


class BuildInfoResponse(BaseModel):
    """Build status information."""

    in_progress: bool = False
    phase: str | None = None  # observe, think, plan, build, execute, verify, learn
    task_description: str | None = None
    started_at: str | None = None
    iteration: int = 0
    score: float = 0.0
    message_count: int = 0


class UIStatusResponse(BaseModel):
    """Combined status response for the UI."""

    connected: bool
    server_status: str
    progenitor_running: bool
    progenitor_status: str = "unknown"  # Raw status from the health document
    uptime_seconds: float
    version: str
    memory_mb: float | None = None
    active_requests: int
    rate_limit_minute_limit: int
    rate_limit_hour_limit: int
    # State data
    cycle_count: int = 0
    thread_id: str | None = None
    messages_count: int = 0
    pid: int | None = None
    last_active: str | None = None
    # Build status
    build: BuildInfoResponse | None = None
    # Lists
    loops: list["LoopInfo"] = []
    agents: list["SubAgentInfo"] = []
    tools: list[dict] = []
    backlog: list["BacklogItemInfo"] = []
    schedule: list["ScheduleItemInfo"] = []
    widgets: list[dict] = []
    sessions: dict = {}
    alerts: AlertsResponse = Field(default_factory=get_alerts_response)


class LoopInfo(BaseModel):
    """Processing loop information (Planner/Executor)."""

    name: str
    status: str
    current_task: str | None = None


class LoopsResponse(BaseModel):
    """Response containing loop status."""

    loops: list[LoopInfo]


class SubAgentInfo(BaseModel):
    """Sub-agent (registered agent) information."""

    name: str
    status: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    transport: str = "stdio"
    task: str | None = None
    progress: int | None = None


class SubAgentsResponse(BaseModel):
    """Response containing registered agents."""

    agents: list[SubAgentInfo]
    total: int


class BacklogItemInfo(BaseModel):
    """Backlog item information."""

    id: str
    type: str
    status: str
    description: str


class BacklogResponse(BaseModel):
    """Response containing backlog items."""

    items: list[BacklogItemInfo]
    total: int


class ScheduleItemInfo(BaseModel):
    """Schedule item information."""

    id: str
    name: str
    schedule: str
    next_run: str | None
    enabled: bool
    paused: bool = False
    last_run: str | None = None
    created_at: str | None = None
    message: str | None = None
    last_run_status: str | None = None
    cron_expression: str | None = None
    scheduled_at: str | None = None
    schedule_type: str | None = None
    timezone: str | None = None


class ScheduleResponse(BaseModel):
    """Response containing schedule items."""

    items: list[ScheduleItemInfo]
    total: int


class WidgetResponse(BaseModel):
    """Unified widget payload."""

    id: str
    type: str
    title: str
    name: str
    status: str
    description: str | None = None
    summary: str | None = None
    logs: list[dict] = []
    updated_at: str | None = None
    data: dict = {}


class WidgetsResponse(BaseModel):
    """Response containing widget entries."""

    items: list[WidgetResponse]
    total: int


class WidgetDeleteResponse(BaseModel):
    """Result of deleting a backend-managed widget."""

    widget_id: str
    removed: bool = True
    already_removed: bool = False
    warning: str | None = None


def _alerts_payload() -> dict[str, object]:
    """Return the full alert payload for REST and SSE consumers."""
    return get_alerts_response().model_dump(mode="json")


class WidgetFieldOptionResponse(BaseModel):
    """Selectable option for a widget field."""

    value: str
    label: str


class WidgetFieldSchemaResponse(BaseModel):
    """Schema for a single editable or readonly widget field."""

    key: str
    label: str
    control: str = "text"
    editable: bool = False
    option_source: str | None = None
    help_text: str | None = None
    options: list[WidgetFieldOptionResponse] = Field(default_factory=list)


class WidgetSectionSchemaResponse(BaseModel):
    """Schema for a collapsible widget details section."""

    id: str
    label: str
    default_expanded: bool = True
    fields: list[WidgetFieldSchemaResponse] = Field(default_factory=list)


class WidgetTabSchemaResponse(BaseModel):
    """Schema for a widget canvas tab."""

    id: str
    label: str


class WidgetSchemaResponse(BaseModel):
    """Details-pane schema for a widget type."""

    type: str
    help_text: str | None = None
    default_tab: str | None = None
    tabs: list[WidgetTabSchemaResponse] = Field(default_factory=list)
    sections: list[WidgetSectionSchemaResponse] = Field(default_factory=list)


class WidgetSchemasResponse(BaseModel):
    """Response containing details-pane schemas by widget type."""

    schemas: list[WidgetSchemaResponse]


class WorkspaceResponse(BaseModel):
    """Workspace bootstrap payload."""

    timestamp: float
    workspace: dict
    health: dict
    sessions: dict
    widgets: list[WidgetResponse]


class PromptFileResponse(BaseModel):
    """Workspace prompt metadata."""

    path: str
    filename: str
    directory: str
    category: str | None = None
    primary_agent: str | None = None
    associated_agents: list[str] = Field(default_factory=list)
    size_bytes: int
    updated_at: float
    preview: str
    content: str


class PromptsResponse(BaseModel):
    """Response containing discovered prompts."""

    prompts: list[PromptFileResponse]
    total: int


class SkillFileResponse(BaseModel):
    """Workspace skill metadata."""

    path: str
    filename: str
    directory: str
    agent: str
    name: str
    title: str
    description: str
    size_bytes: int
    updated_at: float
    preview: str
    content: str


class SkillsResponse(BaseModel):
    """Response containing discovered skills."""

    skills: list[SkillFileResponse]
    total: int


class PromptUpdateRequest(BaseModel):
    """Prompt update payload."""

    path: str
    content: str


class SkillUpdateRequest(BaseModel):
    """Skill update payload."""

    path: str
    content: str


class SkillCreateRequest(BaseModel):
    """Skill creation payload."""

    agent: str
    name: str
    content: str


class WidgetPatchRequest(BaseModel):
    """Patch request for mutable backend-backed widgets."""

    enabled: bool | None = None
    name: str | None = None
    description: str | None = None
    url: str | None = None
    instruction: str | None = None
    schedule_type: str | None = None
    expression: str | None = None
    timezone: str | None = None
    event_type: str | None = None


class ScheduleCreateRequest(BaseModel):
    """Create request for a new schedule entry."""

    name: str
    instruction: str
    schedule_type: str = "cron"
    expression: str
    timezone: str | None = None
    enabled: bool = True


class ScheduleTriggerRequest(BaseModel):
    """Natural-language re-trigger request for an existing schedule."""

    text: str


class EventWidgetCreateRequest(BaseModel):
    """Create request for a new persisted event widget."""

    name: str
    event_type: str
    enabled: bool = True


class KanbanColumnResponse(BaseModel):
    """Kanban column metadata."""

    id: str
    label: str
    description: str
    agent_name: str
    allows_create: bool = False
    stage_kind: str | None = None


class KanbanStatusOptionResponse(BaseModel):
    """Allowed kanban card status."""

    id: str
    label: str
    tone: str = "neutral"


class KanbanFieldSchemaResponse(BaseModel):
    """Schema for an editable kanban card field."""

    key: str
    label: str
    control: str = "text"
    editable: bool = True
    options: list[WidgetFieldOptionResponse] = Field(default_factory=list)


class KanbanCardSchemaResponse(BaseModel):
    """Schema describing editable kanban card state."""

    statuses: list[KanbanStatusOptionResponse] = Field(default_factory=list)
    fields: list[KanbanFieldSchemaResponse] = Field(default_factory=list)


class KanbanCardResponse(BaseModel):
    """Single kanban card entry."""

    id: str
    title: str
    summary: str = ""
    description: str = ""
    column_id: str
    status: str
    agent_name: str
    owner: str = "user"
    project_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    spec_path: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
    position: int = 0
    metadata: dict = Field(default_factory=dict)
    task_path: str | None = None
    task_format: str = "markdown"
    task_content_updated_at: str | None = None


class KanbanBoardResponse(BaseModel):
    """Kanban board payload for the frontend."""

    id: str
    title: str
    updated_at: str
    columns: list[KanbanColumnResponse] = Field(default_factory=list)
    cards: list[KanbanCardResponse] = Field(default_factory=list)
    card_schema: KanbanCardSchemaResponse


class KanbanCardCreateRequest(BaseModel):
    """Create a new kanban card."""

    title: str
    summary: str = ""
    description: str = ""
    owner: str = "user"
    project_id: str | None = None
    labels: list[str] = Field(default_factory=list)


class KanbanCardPatchRequest(BaseModel):
    """Update mutable kanban card fields."""

    title: str | None = None
    summary: str | None = None
    description: str | None = None
    column_id: str | None = None
    status: str | None = None
    agent_name: str | None = None
    owner: str | None = None
    project_id: str | None = None
    labels: list[str] | None = None
    spec_path: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None
    position: int | None = None
    metadata: dict | None = None


class TaskContentResponse(BaseModel):
    """Editable task body stored separately from the kanban card metadata."""

    card_id: str
    path: str
    format: str = "markdown"
    updated_at: str | None = None
    content: str


class TaskContentUpdateRequest(BaseModel):
    """Persisted task-body update payload."""

    content: str


class TaskActivityEntryResponse(BaseModel):
    """One persisted task activity entry."""

    id: str
    card_id: str
    kind: str = "note"
    actor: str = "system"
    message: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskActivityResponse(BaseModel):
    """Response containing a task activity timeline."""

    card_id: str
    items: list[TaskActivityEntryResponse] = Field(default_factory=list)
    total: int = 0


class TaskActivityCreateRequest(BaseModel):
    """Create a user-visible task activity entry."""

    message: str
    actor: str = "user"
    kind: str = "note"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRunRequest(BaseModel):
    """Run an action using the existing IPC message flow."""

    widget_id: str | None = None
    prompt: str | None = None
    code: str | None = None
    content: str | None = None
    input: str | None = None
    language: str | None = None
    broadcast_response: bool = True
    thread_id: str | None = None
    invocation_type: Literal["user", "automation", "system"] = "automation"
    invocation_source: str | None = None
    root_widget_id: str | None = None


class ActionGenerateRequest(BaseModel):
    """Generate content using the existing IPC message flow."""

    widget_id: str | None = None
    prompt: str
    input: str | None = None
    target: str | None = None
    thread_id: str | None = None
    invocation_type: Literal["user", "automation", "system"] = "automation"
    invocation_source: str | None = None
    root_widget_id: str | None = None


class ActionResponse(BaseModel):
    """Action response payload."""

    ok: bool
    widget_id: str | None = None
    thread_id: str | None = None
    response: str


class WorkflowCreateRequest(BaseModel):
    """Create one workflow definition."""

    title: str = "Untitled workflow"
    description: str = ""
    event_type: str = WORKFLOW_ALLOWED_EVENT_TYPES[0]


class WorkflowContentPutRequest(BaseModel):
    """Persist one workflow definition."""

    path: str | None = None
    workflow: dict[str, Any]


class WorkflowManualTriggerRequest(BaseModel):
    """Manual trigger payload for operator testing."""

    event_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowTriggerResponse(BaseModel):
    """Response for manual workflow triggers."""

    ok: bool = True
    queued: bool = False
    run: WorkflowRun | None = None


class WorkflowsResponse(BaseModel):
    """Workflow list payload."""

    items: list[WorkflowListItem] = Field(default_factory=list)


class WorkflowContentResponse(BaseModel):
    """Single workflow definition payload."""

    path: str
    workflow: WorkflowDefinition


class WorkflowRunsResponse(BaseModel):
    """Workflow runs list payload."""

    items: list[WorkflowRun] = Field(default_factory=list)


def _resolve_action_thread_id(thread_id: str | None) -> str:
    """Return a workspace execution thread identifier."""

    normalized = str(thread_id or "").strip()
    if normalized:
        return normalized
    return f"thread_{uuid.uuid4().hex[:12]}"


def _build_action_metadata(
    *,
    thread_id: str,
    invocation_type: Literal["user", "automation", "system"],
    invocation_source: str | None,
    widget_id: str | None,
    root_widget_id: str | None,
    action: str,
) -> dict[str, str]:
    """Build metadata sent through the backend→Progenitor boundary."""

    metadata = {
        "thread_id": thread_id,
        "invocation_type": invocation_type,
        "invocation_source": invocation_source or action,
        "action": action,
    }
    if widget_id:
        metadata["widget_id"] = widget_id
    if root_widget_id:
        metadata["root_widget_id"] = root_widget_id
    return metadata


_workflow_executor: WorkflowExecutor | None = None


async def _workflow_prompt_runner(
    *,
    workflow: WorkflowDefinition,
    node_id: str,
    node,
    input_text: str,
    thread_id: str | None,
) -> PromptExecutionResult:
    widget_id = f"workflow:{workflow.id}:{node_id}"
    root_widget_id = f"workflow:{workflow.id}"
    if isinstance(node, WorkflowNodeCode):
        # Code nodes have no dedicated sandboxed executor (same as the old
        # Workspace canvas): the agent is asked to run the snippet itself
        # through the normal IPC prompt path.
        request = ActionRunRequest(
            widget_id=widget_id,
            code=node.content,
            language=node.language,
            input=input_text,
            broadcast_response=False,
            thread_id=thread_id,
            invocation_type="automation",
            invocation_source=widget_id,
            root_widget_id=root_widget_id,
        )
    elif isinstance(node, WorkflowNodeUser):
        request = ActionRunRequest(
            widget_id=widget_id,
            prompt=build_user_node_instruction(node, input_text),
            broadcast_response=False,
            thread_id=thread_id,
            invocation_type="automation",
            invocation_source=widget_id,
            root_widget_id=root_widget_id,
        )
    else:
        request = ActionRunRequest(
            widget_id=widget_id,
            prompt=node.prompt,
            input=input_text,
            broadcast_response=False,
            thread_id=thread_id,
            invocation_type="automation",
            invocation_source=widget_id,
            root_widget_id=root_widget_id,
        )
    response = await action_run(request)
    return PromptExecutionResult(response=response.response, thread_id=response.thread_id)


def get_workflow_executor() -> WorkflowExecutor:
    """Return the singleton backend workflow executor."""

    global _workflow_executor
    if _workflow_executor is None:
        _workflow_executor = WorkflowExecutor(
            store_getter=get_default_document_store,
            prompt_runner=_workflow_prompt_runner,
            emit_event=lambda event_type, payload: broadcast_ui_event(event_type, payload),
        )
    return _workflow_executor


class TaskInfo(BaseModel):
    """Active task information."""

    id: str
    title: str
    status: str
    description: str | None = None
    progress: int | None = None
    column_id: str | None = None
    owner: str | None = None
    agent_name: str | None = None
    labels: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    task_path: str | None = None


class TasksResponse(BaseModel):
    """Response containing active tasks."""

    tasks: list[TaskInfo]
    total: int


@router.get("/status/snapshot", response_model=UIStatusResponse)
async def get_ui_status_snapshot():
    """
    Get a one-shot snapshot of status information.

    Aggregates health, rate limit, and progenitor status into a single response.
    Uses real data from data/state/ files.

    For continuous updates, use the SSE stream at /status instead.
    """
    from . import main as chat_main

    state_reader = get_state_reader()

    try:
        # Get basic health from backend
        health = await chat_main.health_check_detailed()

        # Get state snapshot (includes loops, agents, backlog, schedule, build)
        snapshot = state_reader.get_snapshot()

        # Determine if progenitor is running from state file
        progenitor_running = snapshot.health.is_running()

        # Build info from snapshot
        build_info = None
        if snapshot.build:
            build_info = BuildInfoResponse(
                in_progress=snapshot.build.in_progress,
                phase=snapshot.build.phase,
                task_description=snapshot.build.task_description,
                started_at=snapshot.build.started_at,
                iteration=snapshot.build.iteration,
                score=snapshot.build.score,
                message_count=snapshot.build.message_count,
            )

        # Build response with full data
        return UIStatusResponse(
            connected=True,
            server_status=health.get("status", "unknown"),
            progenitor_running=progenitor_running,
            progenitor_status=snapshot.health.status,  # Raw status from the health document
            uptime_seconds=health.get("uptime_seconds", 0),
            version=health.get("version", "unknown"),
            memory_mb=health.get("memory", {}).get("rss_mb"),
            active_requests=health.get("shutdown", {}).get("active_requests", 0),
            rate_limit_minute_limit=health.get("rate_limiter", {}).get(
                "requests_per_minute_limit", 60
            ),
            rate_limit_hour_limit=health.get("rate_limiter", {}).get(
                "requests_per_hour_limit", 500
            ),
            # State data
            cycle_count=snapshot.health.cycle_count,
            thread_id=snapshot.health.thread_id,
            messages_count=snapshot.messages_count,
            pid=snapshot.health.pid,
            last_active=snapshot.health.last_active,
            # Build status
            build=build_info,
            # Lists
            loops=[
                LoopInfo(
                    name=loop_item.name,
                    status=loop_item.status,
                    current_task=loop_item.current_task,
                )
                for loop_item in snapshot.loops
            ],
            agents=[
                SubAgentInfo(
                    name=a.name,
                    status="ready" if progenitor_running else "offline",
                    kind=a.kind,
                    version=a.version,
                    description=a.description,
                    transport=a.transport,
                )
                for a in snapshot.agents
            ],
            tools=[
                {"name": tool.name, "description": tool.description, "enabled": tool.enabled}
                for tool in snapshot.tools
            ],
            backlog=[
                BacklogItemInfo(
                    id=b.id, type=b.item_type, status=b.status, description=b.description
                )
                for b in snapshot.backlog
            ],
            schedule=[
                ScheduleItemInfo(
                    id=s.id,
                    name=s.name,
                    schedule=s.schedule,
                    next_run=s.next_run,
                    enabled=s.enabled,
                )
                for s in snapshot.schedule
            ],
            widgets=state_reader.get_widgets(),
            sessions={
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id
                if snapshot.sessions
                else None,
                "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
            },
            alerts=get_alerts_response(),
        )
    except Exception as e:
        logger.error("Failed to get UI status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/loops", response_model=LoopsResponse)
async def get_loops():
    """
    Get status of processing loops (Planner/Executor).
    """
    state_reader = get_state_reader()
    health = state_reader.get_health()
    loops = state_reader.get_loops(health)

    return LoopsResponse(
        loops=[
            LoopInfo(
                name=loop_item.name, status=loop_item.status, current_task=loop_item.current_task
            )
            for loop_item in loops
        ]
    )


@router.get("/agents", response_model=SubAgentsResponse)
async def get_sub_agents():
    """
    Get registered agents from agents.json.

    Returns information about all registered A2A agents.
    """
    state_reader = get_state_reader()
    agents, _ = state_reader.get_registry()
    health = state_reader.get_health()
    progenitor_running = health.is_running()

    # Convert to response format
    agent_infos = []
    for agent in agents:
        if agent.enabled:
            agent_infos.append(
                SubAgentInfo(
                    name=agent.name,
                    status="ready" if progenitor_running else "offline",
                    kind=agent.kind,
                    version=agent.version,
                    description=agent.description,
                    transport=agent.transport,
                    task=None,
                    progress=None,
                )
            )

    return SubAgentsResponse(agents=agent_infos, total=len(agent_infos))


@router.get("/backlog", response_model=BacklogResponse)
async def get_backlog():
    """
    Get backlog items from build_state.json.
    """
    state_reader = get_state_reader()
    backlog = state_reader.get_backlog()

    return BacklogResponse(
        items=[
            BacklogItemInfo(
                id=b.id,
                type=b.item_type,
                status=b.status,
                description=b.description,
            )
            for b in backlog
        ],
        total=len(backlog),
    )


def _schedule_item_to_info(item) -> ScheduleItemInfo:
    """Build a schedule response with a freshly computed next_run.

    ``ScheduleItem.next_run`` falls back to ``last_run`` because the stored entry
    carries no ``next_run`` field. Recompute it here with the same scheduler the
    runtime uses so the UI shows the actual next fire time.
    """
    raw = item.raw if isinstance(item.raw, dict) else {}
    next_run_dt = Scheduler().next_run_for_entry(raw)
    paused = bool(raw.get("paused", not raw.get("enabled", item.enabled)))
    cron_expression = raw.get("cron_expression") or None
    scheduled_at = raw.get("scheduled_at") or None
    schedule_type = raw.get("schedule_type") or (
        "once" if scheduled_at else "cron" if cron_expression else None
    )
    last_run = raw.get("last_run")
    last_run_status = raw.get("last_run_status")
    if (
        last_run_status in {None, "", "idle"}
        and schedule_type == "once"
        and paused
        and last_run
        and next_run_dt is None
    ):
        last_run_status = "completed"
    return ScheduleItemInfo(
        id=item.id,
        name=item.name,
        schedule=item.schedule or cron_expression or scheduled_at or "",
        next_run=next_run_dt.isoformat() if next_run_dt else None,
        enabled=not paused,
        paused=paused,
        last_run=last_run,
        created_at=raw.get("created_at"),
        message=raw.get("message") or item.name,
        cron_expression=cron_expression,
        scheduled_at=scheduled_at,
        schedule_type=schedule_type,
        timezone=raw.get("timezone"),
        last_run_status=last_run_status,
    )


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule():
    """
    Get scheduled items from the shared Postgres-backed schedule document.
    """
    state_reader = get_state_reader()
    schedule = state_reader.get_schedule()

    return ScheduleResponse(
        items=[_schedule_item_to_info(s) for s in schedule],
        total=len(schedule),
    )


def _widget_to_response(widget: dict) -> WidgetResponse:
    """Normalize state-reader widget payloads for API responses."""
    payload = dict(widget)
    return WidgetResponse(
        id=payload.get("id", ""),
        type=payload.get("type", "unknown"),
        title=payload.get("title") or payload.get("name", ""),
        name=payload.get("name", ""),
        status=payload.get("status", "unknown"),
        description=payload.get("description"),
        summary=payload.get("summary"),
        logs=payload.get("logs", []),
        updated_at=payload.get("updated_at"),
        data=payload,
    )


def _kanban_event_type_options() -> list[WidgetFieldOptionResponse]:
    return [
        WidgetFieldOptionResponse(value=value, label=label)
        for value, label in KANBAN_EVENT_TYPE_LABELS.items()
    ]


def _widget_schemas() -> list[WidgetSchemaResponse]:
    """Return schema definitions for workspace details editing."""
    return [
        WidgetSchemaResponse(
            type="prompt",
            help_text="Stores prompt text, targets an agent, and runs or generates code from the latest prompt.",
            default_tab="editor",
            tabs=[
                WidgetTabSchemaResponse(id="editor", label="Editor"),
                WidgetTabSchemaResponse(id="output", label="Output"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title", editable=True),
                        WidgetFieldSchemaResponse(
                            key="targetAgent",
                            label="Target Agent",
                            control="select",
                            editable=True,
                            option_source="agents",
                        ),
                        WidgetFieldSchemaResponse(
                            key="role",
                            label="Role",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="user", label="User"),
                                WidgetFieldOptionResponse(value="system", label="System"),
                                WidgetFieldOptionResponse(value="assistant", label="Assistant"),
                            ],
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="prompt",
                            label="Prompt",
                            control="textarea",
                            editable=True,
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="metadata",
                    label="Metadata",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="sourcePath", label="Source Path"),
                        WidgetFieldSchemaResponse(key="primaryAgent", label="Primary Agent"),
                        WidgetFieldSchemaResponse(
                            key="associatedAgents", label="Associated Agents", control="list"
                        ),
                        WidgetFieldSchemaResponse(
                            key="linkedOutputIds", label="Linked Outputs", control="list"
                        ),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="code",
            help_text="Keeps editable code in the workspace and runs it so linked cards can use the latest output.",
            default_tab="editor",
            tabs=[
                WidgetTabSchemaResponse(id="editor", label="Editor"),
                WidgetTabSchemaResponse(id="output", label="Output"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title", editable=True),
                        WidgetFieldSchemaResponse(key="language", label="Language", editable=True),
                        WidgetFieldSchemaResponse(
                            key="runtimeProfile", label="Runtime Profile", editable=True
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="content", label="Code", control="textarea", editable=True
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="execution",
                    label="Execution",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="lastThreadId", label="Last Thread"),
                        WidgetFieldSchemaResponse(
                            key="outputRefs", label="Outputs", control="list"
                        ),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="webframe",
            help_text="Renders HTML or a URL inside the canvas so you can preview generated UI without leaving the workspace.",
            default_tab="preview",
            tabs=[
                WidgetTabSchemaResponse(id="preview", label="Preview"),
                WidgetTabSchemaResponse(id="source", label="Source"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title", editable=True),
                        WidgetFieldSchemaResponse(
                            key="sourceType",
                            label="Source Type",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="html", label="HTML"),
                                WidgetFieldOptionResponse(value="url", label="URL"),
                            ],
                        ),
                        WidgetFieldSchemaResponse(key="url", label="URL", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="markup",
                    label="Markup",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="html", label="HTML", control="textarea", editable=True
                        ),
                        WidgetFieldSchemaResponse(
                            key="css", label="CSS", control="textarea", editable=True
                        ),
                        WidgetFieldSchemaResponse(
                            key="js", label="JavaScript", control="textarea", editable=True
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="updated",
                    label="Metadata",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="label",
            help_text="Adds workspace notes and labels so you can annotate the canvas without affecting execution.",
            default_tab="content",
            tabs=[
                WidgetTabSchemaResponse(id="content", label="Content"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title", editable=True),
                        WidgetFieldSchemaResponse(
                            key="text", label="Text", control="textarea", editable=True
                        ),
                        WidgetFieldSchemaResponse(
                            key="variant",
                            label="Variant",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="note", label="Note"),
                                WidgetFieldOptionResponse(value="warning", label="Warning"),
                                WidgetFieldOptionResponse(value="info", label="Info"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="group",
            help_text="Organizes related cards into a visual group so larger workflows stay easier to scan.",
            default_tab="content",
            tabs=[
                WidgetTabSchemaResponse(id="content", label="Content"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title", editable=True),
                        WidgetFieldSchemaResponse(
                            key="description",
                            label="Description",
                            control="textarea",
                            editable=True,
                        ),
                        WidgetFieldSchemaResponse(key="childIds", label="Children", control="list"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="schedule",
            help_text="Creates a timed automation that runs Progenitor once, on an interval, or on a cron schedule.",
            default_tab="details",
            tabs=[
                WidgetTabSchemaResponse(id="details", label="Details"),
                WidgetTabSchemaResponse(id="output", label="Output"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Name", editable=True),
                        WidgetFieldSchemaResponse(
                            key="enabled", label="Enabled", control="boolean", editable=True
                        ),
                        WidgetFieldSchemaResponse(
                            key="scheduleType",
                            label="Schedule Type",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="cron", label="Cron"),
                                WidgetFieldOptionResponse(value="interval", label="Interval"),
                                WidgetFieldOptionResponse(value="once", label="Once"),
                            ],
                        ),
                        WidgetFieldSchemaResponse(
                            key="expression", label="Expression", editable=True
                        ),
                        WidgetFieldSchemaResponse(key="timezone", label="Timezone", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Task",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="instruction",
                            label="Instruction",
                            control="textarea",
                            editable=True,
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="nextRunAt", label="Next Run"),
                        WidgetFieldSchemaResponse(key="lastRunAt", label="Last Run"),
                        WidgetFieldSchemaResponse(key="lastRunStatus", label="Last Run Status"),
                        WidgetFieldSchemaResponse(key="saveError", label="Save Error"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="event",
            help_text="Listens for a workspace event and triggers linked cards when that event fires.",
            default_tab="details",
            tabs=[
                WidgetTabSchemaResponse(id="details", label="Details"),
                WidgetTabSchemaResponse(id="output", label="Output"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Name", editable=True),
                        WidgetFieldSchemaResponse(
                            key="enabled", label="Enabled", control="boolean", editable=True
                        ),
                        WidgetFieldSchemaResponse(
                            key="eventType",
                            label="Event Type",
                            control="select",
                            editable=True,
                            options=_kanban_event_type_options(),
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="triggerCount", label="Trigger Count"),
                        WidgetFieldSchemaResponse(key="lastTriggeredAt", label="Last Triggered"),
                        WidgetFieldSchemaResponse(key="lastRunStatus", label="Last Run Status"),
                        WidgetFieldSchemaResponse(key="saveError", label="Save Error"),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="user",
            help_text="Configures a user-facing tool call so the current run can send a message, notification, or question.",
            default_tab="details",
            tabs=[
                WidgetTabSchemaResponse(id="details", label="Details"),
                WidgetTabSchemaResponse(id="output", label="Output"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Name", editable=True),
                        WidgetFieldSchemaResponse(
                            key="messageType",
                            label="Message Type",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="say_to_user", label="say_to_user"),
                                WidgetFieldOptionResponse(value="notify_user", label="notify_user"),
                                WidgetFieldOptionResponse(value="ask_user", label="ask_user"),
                            ],
                        ),
                        WidgetFieldSchemaResponse(
                            key="notificationTitle",
                            label="Notification Title",
                            editable=True,
                        ),
                        WidgetFieldSchemaResponse(
                            key="level",
                            label="Notification Level",
                            control="select",
                            editable=True,
                            options=[
                                WidgetFieldOptionResponse(value="info", label="Info"),
                                WidgetFieldOptionResponse(value="warning", label="Warning"),
                                WidgetFieldOptionResponse(value="error", label="Error"),
                                WidgetFieldOptionResponse(value="success", label="Success"),
                            ],
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="message",
                            label="Message / Body",
                            control="textarea",
                            editable=True,
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="lastSentAt", label="Last Sent"),
                        WidgetFieldSchemaResponse(
                            key="summaryState", label="Summary", control="textarea"
                        ),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="agent",
            help_text="Shows a linked agent, its recent activity, and the latest thread-linked output for this card.",
            default_tab="overview",
            tabs=[
                WidgetTabSchemaResponse(id="overview", label="Overview"),
                WidgetTabSchemaResponse(id="details", label="Details"),
                WidgetTabSchemaResponse(id="capabilities", label="Capabilities"),
                WidgetTabSchemaResponse(id="skills", label="Skills"),
                WidgetTabSchemaResponse(id="logs", label="Logs"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title"),
                        WidgetFieldSchemaResponse(key="agentId", label="Agent"),
                        WidgetFieldSchemaResponse(key="transport", label="Transport"),
                        WidgetFieldSchemaResponse(key="url", label="URL"),
                        WidgetFieldSchemaResponse(key="agent_card_url", label="Agent Card URL"),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    fields=[
                        WidgetFieldSchemaResponse(key="agentStatus", label="Status"),
                        WidgetFieldSchemaResponse(key="currentThreadId", label="Thread ID"),
                        WidgetFieldSchemaResponse(
                            key="summaryState", label="Summary", control="textarea"
                        ),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="mcp",
            help_text="Shows an MCP integration, its recent activity, and the latest thread-linked output for this card.",
            default_tab="overview",
            tabs=[
                WidgetTabSchemaResponse(id="overview", label="Overview"),
                WidgetTabSchemaResponse(id="details", label="Details"),
                WidgetTabSchemaResponse(id="tools", label="Tools"),
                WidgetTabSchemaResponse(id="resources", label="Resources"),
                WidgetTabSchemaResponse(id="prompts", label="Prompts"),
                WidgetTabSchemaResponse(id="logs", label="Logs"),
            ],
            sections=[
                WidgetSectionSchemaResponse(
                    id="identity",
                    label="Identity",
                    fields=[
                        WidgetFieldSchemaResponse(key="title", label="Title"),
                        WidgetFieldSchemaResponse(key="agentId", label="Tool"),
                        WidgetFieldSchemaResponse(key="transport", label="Transport"),
                        WidgetFieldSchemaResponse(key="url", label="URL", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="config",
                    label="Configuration",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="description",
                            label="Description",
                            control="textarea",
                            editable=True,
                        ),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    fields=[
                        WidgetFieldSchemaResponse(
                            key="enabled",
                            label="Enabled",
                            control="boolean",
                            editable=True,
                        ),
                        WidgetFieldSchemaResponse(key="agentStatus", label="Status"),
                        WidgetFieldSchemaResponse(key="currentThreadId", label="Thread ID"),
                        WidgetFieldSchemaResponse(
                            key="summaryState", label="Summary", control="textarea"
                        ),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
    ]


def _kanban_default_columns() -> list[dict]:
    """Return the default kanban workflow columns."""
    return [
        {
            "id": "backlog",
            "label": "Backlog",
            "description": "New user requests waiting to be shaped into delivery work.",
            "agent_name": "user",
            "allows_create": True,
            "stage_kind": "intake",
        },
        {
            "id": "plan",
            "label": "Plan",
            "description": "Turn the chosen approach into specs and implementation notes.",
            "agent_name": "planner_agent",
            "allows_create": False,
            "stage_kind": "planning",
        },
        {
            "id": "build",
            "label": "Build",
            "description": "Implement the change, create code, and prepare a pull request.",
            "agent_name": "builder_progenitor",
            "allows_create": False,
            "stage_kind": "build",
        },
        {
            "id": "validate",
            "label": "Validate",
            "description": "User-facing validation and final approval before the work is considered done.",
            "agent_name": "validate_agent",
            "allows_create": False,
            "stage_kind": "validate",
        },
        {
            "id": "done",
            "label": "Done",
            "description": "Merged and deployed work that no longer needs active handling.",
            "agent_name": "release_agent",
            "allows_create": False,
            "stage_kind": "done",
        },
    ]


def _canonical_kanban_column_id(column_id: object) -> str:
    """Map legacy kanban columns onto the current workflow."""
    normalized = str(column_id or "backlog").strip().lower()
    return {
        "research": "plan",
        "review": "validate",
        "test": "validate",
    }.get(normalized, normalized or "backlog")


def _kanban_status_options() -> list[KanbanStatusOptionResponse]:
    """Return allowed kanban card statuses."""
    return [
        KanbanStatusOptionResponse(id="draft", label="Draft", tone="neutral"),
        KanbanStatusOptionResponse(id="idle", label="Idle", tone="neutral"),
        KanbanStatusOptionResponse(id="running", label="Running", tone="info"),
        KanbanStatusOptionResponse(id="complete", label="Complete", tone="success"),
        KanbanStatusOptionResponse(id="failed", label="Failed", tone="error"),
        KanbanStatusOptionResponse(id="blocked", label="Blocked", tone="warning"),
    ]


def _kanban_card_schema(columns: list[dict]) -> KanbanCardSchemaResponse:
    """Return field schema for kanban cards."""
    status_options = _kanban_status_options()
    return KanbanCardSchemaResponse(
        statuses=status_options,
        fields=[
            KanbanFieldSchemaResponse(key="title", label="Title"),
            KanbanFieldSchemaResponse(key="summary", label="Summary"),
            KanbanFieldSchemaResponse(key="description", label="Description", control="textarea"),
            KanbanFieldSchemaResponse(
                key="column_id",
                label="Column",
                control="select",
                options=[
                    WidgetFieldOptionResponse(value=column["id"], label=column["label"])
                    for column in columns
                ],
            ),
            KanbanFieldSchemaResponse(
                key="status",
                label="Status",
                control="select",
                options=[
                    WidgetFieldOptionResponse(value=status.id, label=status.label)
                    for status in status_options
                ],
            ),
            KanbanFieldSchemaResponse(key="agent_name", label="Assigned Agent"),
            KanbanFieldSchemaResponse(key="owner", label="Owner"),
            KanbanFieldSchemaResponse(key="project_id", label="Project"),
            KanbanFieldSchemaResponse(key="spec_path", label="Spec File"),
            KanbanFieldSchemaResponse(key="branch_name", label="Branch"),
            KanbanFieldSchemaResponse(key="pr_url", label="Pull Request URL"),
            KanbanFieldSchemaResponse(key="last_error", label="Last Error", control="textarea"),
        ],
    )


def _kanban_default_board() -> dict:
    """Return a default kanban board payload."""
    return {
        "id": "progenitor-kanban",
        "title": "Delivery Kanban",
        "updated_at": utcnow().isoformat(),
        "columns": _kanban_default_columns(),
        "cards": [],
    }


def _normalize_string_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []

    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_non_negative_int(value: object) -> int:
    """Normalize persisted integer fields that should never be negative."""
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _normalize_kanban_card(card: dict, columns: dict[str, dict]) -> dict:
    """Normalize raw kanban card data into a stable API shape."""
    now = utcnow().isoformat()
    column_id = _canonical_kanban_column_id(card.get("column_id"))
    if column_id not in columns:
        column_id = "backlog"

    allowed_statuses = {status.id for status in _kanban_status_options()}
    status = str(card.get("status") or "idle").strip().lower()
    if status not in allowed_statuses:
        status = "idle"

    title = str(card.get("title") or "").strip() or "Untitled card"
    metadata = card.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        "id": str(card.get("id") or f"card_{uuid.uuid4().hex[:12]}"),
        "title": title,
        "summary": str(card.get("summary") or "").strip(),
        "description": str(card.get("description") or ""),
        "column_id": column_id,
        "status": status,
        "agent_name": str(card.get("agent_name") or columns[column_id]["agent_name"]),
        "owner": str(card.get("owner") or "user"),
        "project_id": str(card.get("project_id") or "").strip() or None,
        "labels": _normalize_string_list(card.get("labels")),
        "spec_path": str(card.get("spec_path") or "").strip() or None,
        "branch_name": str(card.get("branch_name") or "").strip() or None,
        "pr_url": str(card.get("pr_url") or "").strip() or None,
        "started_at": str(card.get("started_at") or "").strip() or None,
        "completed_at": str(card.get("completed_at") or "").strip() or None,
        "last_error": str(card.get("last_error") or "").strip() or None,
        "created_at": str(card.get("created_at") or now),
        "updated_at": str(card.get("updated_at") or now),
        "position": _normalize_non_negative_int(card.get("position")),
        "metadata": metadata,
    }


def _resequence_kanban_cards(cards: list[dict], columns: list[dict]) -> list[dict]:
    """Sort cards into column order and assign dense per-column positions."""
    column_order = {column["id"]: index for index, column in enumerate(columns)}
    grouped_cards: dict[str, list[dict]] = {column["id"]: [] for column in columns}

    ordered_cards = sorted(
        cards,
        key=lambda card: (
            column_order.get(card["column_id"], len(columns)),
            _normalize_non_negative_int(card.get("position")),
            str(card.get("created_at") or ""),
            card["id"],
        ),
    )
    for card in ordered_cards:
        grouped_cards.setdefault(card["column_id"], []).append(dict(card))

    normalized_cards: list[dict] = []
    for column in columns:
        for index, card in enumerate(grouped_cards.get(column["id"], [])):
            card["position"] = index
            normalized_cards.append(card)
    return normalized_cards


def _reorder_kanban_cards(
    cards: list[dict],
    columns: list[dict],
    card_id: str,
    target_column_id: str,
    target_position: int,
) -> list[dict]:
    """Move a card to a specific position within a target column."""
    grouped_cards: dict[str, list[dict]] = {column["id"]: [] for column in columns}
    moving_card: dict | None = None

    for card in _resequence_kanban_cards(cards, columns):
        if card["id"] == card_id:
            moving_card = dict(card)
            continue
        grouped_cards.setdefault(card["column_id"], []).append(dict(card))

    if moving_card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    moving_card["column_id"] = target_column_id
    target_cards = grouped_cards.setdefault(target_column_id, [])
    insert_at = max(0, min(target_position, len(target_cards)))
    target_cards.insert(insert_at, moving_card)

    normalized_cards: list[dict] = []
    for column in columns:
        for index, card in enumerate(grouped_cards.get(column["id"], [])):
            card["position"] = index
            normalized_cards.append(card)
    return normalized_cards


def _normalize_kanban_board(data: object) -> dict:
    """Normalize persisted kanban board data."""
    defaults = _kanban_default_board()
    raw = data if isinstance(data, dict) else {}
    columns = _kanban_default_columns()
    column_map = {column["id"]: column for column in columns}
    cards = [
        _normalize_kanban_card(card, column_map)
        for card in raw.get("cards", [])
        if isinstance(card, dict)
    ]
    cards = _resequence_kanban_cards(cards, columns)
    return {
        "id": str(raw.get("id") or defaults["id"]),
        "title": str(raw.get("title") or defaults["title"]),
        "updated_at": str(raw.get("updated_at") or defaults["updated_at"]),
        "columns": columns,
        "cards": cards,
    }


def _kanban_default_board_document() -> dict:
    """Return a freshly normalized empty board for first-time seeding."""
    return _normalize_kanban_board(None)


def _require_document_store(state_reader: StateReader):
    """Return the backend document store or fail loudly.

    Tasks are database-only: the board, task bodies, and activity all live in the
    shared Postgres ``store`` (the same documents the runtime agent reads/edits).
    There is no on-disk fallback, so a missing store is a hard 503.
    """
    if state_reader.document_store is None:
        raise HTTPException(status_code=503, detail="Document store is unavailable")
    return state_reader.document_store


def _kanban_board_store_key() -> str:
    """Return the shared-store key for the kanban board structure document.

    Holds only board-level data (columns + ``card_order``); each card lives in
    its own ``kanban_card:<id>`` document so the agent edits one task per file
    and writes touch a single row.
    """
    return "kanban_board"


_KANBAN_CARD_KEY_PREFIX = "kanban_card:"


def _kanban_card_store_key(card_id: str) -> str:
    """Return the shared-store key for one kanban card document."""
    return f"{_KANBAN_CARD_KEY_PREFIX}{card_id}"


# Fields whose change fires a `kanban.card.updated` trigger. Keys are also the
# values: kept as a set-like map for symmetry with the historical body-field
# names, which happen to match the normalized card's own field names.
_KANBAN_CARD_CONTENT_FIELDS = (
    "title",
    "summary",
    "description",
    "agent_name",
    "owner",
    "labels",
    "spec_path",
    "branch_name",
    "pr_url",
    "started_at",
    "completed_at",
    "last_error",
    "metadata",
)

# Last-dispatched state per card id, used to detect kanban.card.* changes that
# didn't go through the REST routes below (an agent writing
# /state/tasks/<id>.md directly bypasses them entirely). Guarded by
# _kanban_card_trigger_lock wherever it's read-then-written, so a REST-driven
# change and the store-change reconciler picking up its own resulting NOTIFY
# can't both decide to dispatch the same change.
_last_known_kanban_cards: dict[str, dict] = {}
_kanban_card_trigger_lock = asyncio.Lock()
_kanban_card_cache_seeded = False


def _remember_kanban_card_state(card_id: str, card: dict | None) -> None:
    if card is None:
        _last_known_kanban_cards.pop(card_id, None)
    else:
        _last_known_kanban_cards[card_id] = dict(card)


def _kanban_card_change_triggers(previous_card: dict, refreshed_card: dict, now: str) -> list[dict]:
    """Diff a kanban card's before/after state into kanban.card.* triggers.

    Shared by the REST kanban routes and the generic store-change reconciler
    (_reconcile_kanban_card_triggers) so a card edit fires the same triggers
    whether it came from the UI or an agent writing /state/tasks/<id>.md
    directly.
    """
    triggers: list[dict] = []
    changed_fields = [
        field
        for field in _KANBAN_CARD_CONTENT_FIELDS
        if previous_card.get(field) != refreshed_card.get(field)
    ]
    if changed_fields:
        triggers.append(
            {
                "event_type": "kanban.card.updated",
                "timestamp": now,
                "payload": {
                    "event_type": "kanban.card.updated",
                    "card": refreshed_card,
                    "previous_card": previous_card,
                    "changed_fields": changed_fields,
                },
            }
        )
    if previous_card.get("column_id") != refreshed_card.get("column_id") or previous_card.get(
        "position"
    ) != refreshed_card.get("position"):
        triggers.append(
            {
                "event_type": "kanban.card.moved",
                "timestamp": now,
                "payload": {
                    "event_type": "kanban.card.moved",
                    "card": refreshed_card,
                    "previous_card": previous_card,
                    "from": {
                        "column_id": previous_card.get("column_id"),
                        "position": previous_card.get("position"),
                    },
                    "to": {
                        "column_id": refreshed_card.get("column_id"),
                        "position": refreshed_card.get("position"),
                    },
                },
            }
        )
    if previous_card.get("status") != refreshed_card.get("status"):
        triggers.append(
            {
                "event_type": "kanban.card.status_changed",
                "timestamp": now,
                "payload": {
                    "event_type": "kanban.card.status_changed",
                    "card": refreshed_card,
                    "previous_card": previous_card,
                    "from_status": previous_card.get("status"),
                    "to_status": refreshed_card.get("status"),
                },
            }
        )
    return triggers


def _enrich_kanban_workflow_triggers(triggers: list[dict], now: str) -> list[dict]:
    return [
        {
            **trigger,
            "payload": {
                **(trigger.get("payload") or {}),
                "timestamp": trigger.get("timestamp") or now,
                "source": "kanban",
            },
        }
        for trigger in triggers
    ]


async def _apply_kanban_card_diff_effects(
    state_reader: StateReader,
    *,
    previous_card: dict,
    refreshed_card: dict,
    now: str,
) -> None:
    """Fire event-widget triggers, workflow triggers, task-activity entries,
    and the activity broadcast for one card's before/after diff.
    """
    card_id = str(refreshed_card.get("id") or "").strip()
    triggers = _kanban_card_change_triggers(previous_card, refreshed_card, now)
    _apply_event_widget_triggers(state_reader, triggers)
    await _dispatch_workflow_triggers(_enrich_kanban_workflow_triggers(triggers, now))
    if previous_card.get("status") != refreshed_card.get("status"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="status",
            actor="system",
            message=(
                f"Status changed from {previous_card.get('status') or 'unknown'} "
                f"to {refreshed_card.get('status') or 'unknown'}."
            ),
            metadata={
                "from_status": previous_card.get("status"),
                "to_status": refreshed_card.get("status"),
            },
            created_at=now,
        )
    if previous_card.get("column_id") != refreshed_card.get("column_id"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="move",
            actor="system",
            message=(
                f"Moved from {previous_card.get('column_id') or 'unknown'} "
                f"to {refreshed_card.get('column_id') or 'unknown'}."
            ),
            metadata={
                "from_column_id": previous_card.get("column_id"),
                "to_column_id": refreshed_card.get("column_id"),
            },
            created_at=now,
        )
    if previous_card.get("title") != refreshed_card.get("title"):
        _append_task_activity_entry(
            state_reader,
            card_id=card_id,
            kind="update",
            actor="system",
            message=f"Renamed task to {refreshed_card.get('title') or 'Untitled task'}.",
            metadata={"field": "title"},
            created_at=now,
        )
    if (
        previous_card.get("status") != refreshed_card.get("status")
        or previous_card.get("column_id") != refreshed_card.get("column_id")
        or previous_card.get("title") != refreshed_card.get("title")
    ):
        broadcast_task_activity(card_id)


async def _reconcile_kanban_card_triggers(changed_key: str | None) -> None:
    """Catch kanban card changes that bypassed the REST routes below.

    The REST routes (create/patch/delete) dispatch triggers inline and then
    record the resulting state via _remember_kanban_card_state. But the
    runtime agent's actual day-to-day mechanism for touching cards is
    writing straight to /state/tasks/<id>.md, which persists via
    StateManager straight to Postgres -- it never goes through these routes.
    This reconciler runs off the same store-change watcher that already
    pushes kanban.snapshot live (see _queue_state_watch_status), diffing the
    current board against the last state we dispatched triggers for and
    firing whatever a REST-driven edit would have fired, so an agent marking
    a card done fires kanban.card.moved (and any workflow watching for it)
    exactly like a drag-and-drop move does.

    Guarded by _kanban_card_trigger_lock so this can't double-fire against a
    REST route handling the same underlying change concurrently: whichever
    one observes the pre-change state first dispatches and updates the
    cache; the other then sees no diff and no-ops.
    """
    global _kanban_card_cache_seeded
    if not (
        changed_key is None
        or changed_key == _kanban_board_store_key()
        or changed_key.startswith(_KANBAN_CARD_KEY_PREFIX)
    ):
        return
    state_reader = get_state_reader()
    try:
        board = _read_kanban_board(state_reader)
    except Exception:
        logger.debug("Failed to read kanban board for trigger reconciliation", exc_info=True)
        return
    current_by_id = {
        str(card["id"]): card for card in board["cards"] if str(card.get("id") or "").strip()
    }
    now = utcnow().isoformat()
    async with _kanban_card_trigger_lock:
        if not _kanban_card_cache_seeded:
            # First run since backend startup: the in-memory cache has no
            # history, so treat the current board as the known baseline
            # rather than firing a false "created" trigger for every
            # pre-existing card.
            for card_id, card in current_by_id.items():
                _remember_kanban_card_state(card_id, card)
            _kanban_card_cache_seeded = True
            return

        known_ids = set(_last_known_kanban_cards)
        current_ids = set(current_by_id)

        for card_id in current_ids - known_ids:
            card = current_by_id[card_id]
            _remember_kanban_card_state(card_id, card)
            trigger = {
                "event_type": "kanban.card.created",
                "timestamp": now,
                "payload": {"event_type": "kanban.card.created", "card": card},
            }
            _apply_event_widget_triggers(state_reader, [trigger])
            await _dispatch_workflow_triggers(_enrich_kanban_workflow_triggers([trigger], now))

        for card_id in known_ids - current_ids:
            previous = _last_known_kanban_cards.get(card_id)
            _remember_kanban_card_state(card_id, None)
            if previous is None:
                continue
            trigger = {
                "event_type": "kanban.card.deleted",
                "timestamp": now,
                "payload": {"event_type": "kanban.card.deleted", "card": previous},
            }
            _apply_event_widget_triggers(state_reader, [trigger])
            await _dispatch_workflow_triggers(_enrich_kanban_workflow_triggers([trigger], now))

        for card_id in current_ids & known_ids:
            previous = _last_known_kanban_cards.get(card_id)
            refreshed = current_by_id[card_id]
            if previous is None or previous == refreshed:
                continue
            _remember_kanban_card_state(card_id, refreshed)
            await _apply_kanban_card_diff_effects(
                state_reader, previous_card=previous, refreshed_card=refreshed, now=now
            )


def _task_content_store_key(card_id: str) -> str:
    """Return the shared-store key for one task body."""
    return f"kanban_task_content:{card_id}"


def _task_content_public_path(card_id: str) -> str:
    """Return the user-facing relative path for one task body."""
    return f"tasks/{card_id}.md"


def _task_activity_store_key(card_id: str) -> str:
    """Return the shared-store key for task activity."""
    return f"kanban_task_activity:{card_id}"


def _render_task_content_value(value: object) -> str:
    """Normalize stored task-body payloads into plain text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("content") or "")
    return ""


def _default_task_content(card: dict, columns: list[dict] | None = None) -> str:
    """Build a markdown task brief from the kanban card metadata."""
    column_map = {column["id"]: column for column in columns or _kanban_default_columns()}
    column = column_map.get(str(card.get("column_id") or "backlog"), {})
    summary = str(card.get("summary") or "").strip() or "_No summary yet._"
    description = str(card.get("description") or "").strip() or (
        "_Add the implementation brief, acceptance criteria, or notes here._"
    )
    labels = ", ".join(_normalize_string_list(card.get("labels"))) or "none"
    references = [
        ("Spec", str(card.get("spec_path") or "").strip() or "not linked"),
        ("Branch", str(card.get("branch_name") or "").strip() or "not linked"),
        ("Pull request", str(card.get("pr_url") or "").strip() or "not linked"),
    ]
    reference_lines = "\n".join(f"- {label}: {value}" for label, value in references)
    return "\n".join(
        [
            f"# {str(card.get('title') or 'Untitled task').strip()}",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Task",
            "",
            description,
            "",
            "## Workflow",
            "",
            f"- Column: {column.get('label') or str(card.get('column_id') or 'backlog')}",
            f"- Status: {str(card.get('status') or 'idle')}",
            f"- Owner: {str(card.get('owner') or 'user')}",
            f"- Agent: {str(card.get('agent_name') or column.get('agent_name') or 'user')}",
            f"- Labels: {labels}",
            "",
            "## References",
            "",
            reference_lines,
        ]
    ).strip()


def _read_task_content(state_reader: StateReader, card: dict) -> TaskContentResponse:
    """Read a task body from backend state, falling back to derived markdown."""
    card_id = str(card.get("id") or "").strip()
    if not card_id:
        raise HTTPException(status_code=400, detail="Kanban card is missing an id")

    store = _require_document_store(state_reader)
    updated_at: str | None = None
    content: str | None = None
    document = store.get_document(
        PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        None,
    )
    if document is not None:
        content = _render_task_content_value(document.payload)
        if document.updated_at is not None:
            updated_at = document.updated_at.isoformat()

    if content is None:
        content = _default_task_content(card)

    return TaskContentResponse(
        card_id=card_id,
        path=_task_content_public_path(card_id),
        updated_at=updated_at,
        content=content,
    )


def _normalize_task_activity_entries(value: object, card_id: str) -> list[dict[str, Any]]:
    """Normalize persisted task activity entries into a stable API shape."""
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        created_at = str(entry.get("created_at") or utcnow().isoformat())
        normalized.append(
            {
                "id": str(entry.get("id") or f"task_activity_{uuid.uuid4().hex[:12]}"),
                "card_id": card_id,
                "kind": str(entry.get("kind") or "note"),
                "actor": str(entry.get("actor") or "system"),
                "message": str(entry.get("message") or "").strip(),
                "created_at": created_at,
                "metadata": entry.get("metadata")
                if isinstance(entry.get("metadata"), dict)
                else {},
            }
        )
    normalized.sort(key=lambda item: str(item.get("created_at") or ""))
    return [item for item in normalized if item["message"]]


def _read_task_activity_entries(state_reader: StateReader, card_id: str) -> list[dict[str, Any]]:
    """Read persisted task activity entries."""
    store = _require_document_store(state_reader)
    document = store.get_document(
        PROGENITOR_STATE_PREFIX,
        _task_activity_store_key(card_id),
        [],
    )
    entries = document.payload if document is not None else []
    return _normalize_task_activity_entries(entries, card_id)


def _write_task_activity_entries(
    state_reader: StateReader,
    card_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist task activity entries."""
    store = _require_document_store(state_reader)
    normalized = _normalize_task_activity_entries(entries, card_id)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        _task_activity_store_key(card_id),
        normalized,
    )
    return normalized


def _append_task_activity_entry(
    state_reader: StateReader,
    *,
    card_id: str,
    message: str,
    actor: str = "system",
    kind: str = "note",
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> TaskActivityEntryResponse:
    """Append one activity entry to the task timeline."""
    normalized_message = str(message or "").strip()
    if not normalized_message:
        raise HTTPException(status_code=400, detail="Task activity message is required")

    entries = _read_task_activity_entries(state_reader, card_id)
    entry: dict[str, Any] = {
        "id": f"task_activity_{uuid.uuid4().hex[:12]}",
        "card_id": card_id,
        "kind": kind,
        "actor": actor,
        "message": normalized_message,
        "created_at": created_at or utcnow().isoformat(),
        "metadata": metadata or {},
    }
    entries.append(entry)
    _write_task_activity_entries(state_reader, card_id, entries)
    return TaskActivityEntryResponse(**entry)


def _task_activity_response(state_reader: StateReader, card_id: str) -> TaskActivityResponse:
    """Build a task activity response for one card."""
    items = [
        TaskActivityEntryResponse(**entry)
        for entry in _read_task_activity_entries(state_reader, card_id)
    ]
    return TaskActivityResponse(card_id=card_id, items=items, total=len(items))


def _write_task_content(
    state_reader: StateReader, card_id: str, content: str
) -> TaskContentResponse:
    """Persist a task body to the shared document store."""
    store = _require_document_store(state_reader)
    normalized = content.rstrip()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        {"content": normalized},
    )
    document = store.get_document(
        PROGENITOR_STATE_PREFIX,
        _task_content_store_key(card_id),
        None,
    )
    updated_at = document.updated_at.isoformat() if document and document.updated_at else None

    return TaskContentResponse(
        card_id=card_id,
        path=_task_content_public_path(card_id),
        updated_at=updated_at,
        content=normalized,
    )


def _find_kanban_card(state_reader: StateReader, card_id: str) -> dict[str, Any]:
    """Look up one kanban card or raise a 404."""
    board = _read_kanban_board(state_reader)
    card = next((item for item in board["cards"] if item["id"] == card_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")
    return cast(dict[str, Any], card)


def _kanban_card_response(state_reader: StateReader, card: dict) -> KanbanCardResponse:
    """Attach task-body metadata to a kanban card response."""
    task_content = _read_task_content(state_reader, card)
    return KanbanCardResponse(
        **card,
        task_path=task_content.path,
        task_format=task_content.format,
        task_content_updated_at=task_content.updated_at,
    )


def _kanban_board_response(
    state_reader: StateReader, board: dict | None = None
) -> KanbanBoardResponse:
    """Build the canonical kanban response payload."""
    snapshot = _read_kanban_board(state_reader) if board is None else board
    return KanbanBoardResponse(
        id=snapshot["id"],
        title=snapshot["title"],
        updated_at=snapshot["updated_at"],
        columns=[KanbanColumnResponse(**column) for column in snapshot["columns"]],
        cards=[_kanban_card_response(state_reader, card) for card in snapshot["cards"]],
        card_schema=_kanban_card_schema(snapshot["columns"]),
    )


def _event_widget_path(state_reader: StateReader) -> Path:
    """Return the persisted event widget state path."""
    return state_reader.config_dir / "event_widgets.json"


def _read_kanban_card_doc(store: Any, card_id: str) -> dict | None:
    """Read one persisted card document (the ``kanban_card:<id>`` row)."""
    document = store.get_document(
        PROGENITOR_STATE_PREFIX,
        _kanban_card_store_key(card_id),
        None,
    )
    if document is None or not isinstance(document.payload, dict):
        return None
    return document.payload


def _migrate_inline_cards(store: Any, structure: dict, inline_cards: list) -> list[str]:
    """Split a legacy inline ``cards`` array into per-card documents.

    Older boards stored every card inside the single ``kanban_board`` document.
    The first read after upgrade writes each card to its own ``kanban_card:<id>``
    row, records the ordering in ``card_order``, and drops the inline array.
    """
    card_order: list[str] = []
    for card in inline_cards:
        if not isinstance(card, dict):
            continue
        card_id = str(card.get("id") or "").strip()
        if not card_id or card_id in card_order:
            continue
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            _kanban_card_store_key(card_id),
            card,
        )
        card_order.append(card_id)
    structure["card_order"] = card_order
    structure.pop("cards", None)
    store.save_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure)
    return card_order


def _read_kanban_board(state_reader: StateReader) -> dict:
    """Assemble the kanban board from its structure + per-card documents.

    The board structure (columns + ``card_order``) is one document under the
    shared ``("progenitor", "state")`` namespace; each card is its own
    ``kanban_card:<id>`` document so the runtime agent edits one task per file
    and writes touch a single row. On first read we seed an empty board, and we
    transparently migrate any legacy inline-``cards`` board into per-card rows.
    """
    store = _require_document_store(state_reader)
    document = store.get_document(
        PROGENITOR_STATE_PREFIX,
        _kanban_board_store_key(),
        None,
    )
    if document is None:
        normalized = _kanban_default_board_document()
        structure = {k: v for k, v in normalized.items() if k != "cards"}
        structure["card_order"] = []
        store.save_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure)
        return normalized

    structure = dict(document.payload) if isinstance(document.payload, dict) else {}
    inline_cards = structure.get("cards")
    if isinstance(inline_cards, list) and inline_cards and "card_order" not in structure:
        card_order = _migrate_inline_cards(store, structure, inline_cards)
    else:
        order = structure.get("card_order")
        card_order = (
            [str(cid).strip() for cid in order if str(cid).strip()]
            if isinstance(order, list)
            else []
        )

    cards = [card for cid in card_order if (card := _read_kanban_card_doc(store, cid)) is not None]
    assembled = {**structure, "cards": cards}
    assembled.pop("card_order", None)
    return _normalize_kanban_board(assembled)


def _normalize_event_widget_logs(data: object) -> list[dict]:
    if not isinstance(data, list):
        return []

    normalized: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        normalized.append(
            {
                "time": entry.get("time") or entry.get("timestamp"),
                "timestamp": entry.get("timestamp") or entry.get("time"),
                "level": entry.get("level", "info"),
                "source": entry.get("source", "kanban"),
                "message": entry.get("message") or "",
            }
        )
    return normalized[:MAX_EVENT_WIDGET_LOGS]


def _normalize_event_widget_record(entry: dict) -> dict:
    event_type = str(entry.get("event_type") or "kanban.card.created").strip().lower()
    name = str(entry.get("name") or event_type or "Event").strip()
    last_payload = entry.get("last_payload")
    logs = _normalize_event_widget_logs(entry.get("logs"))
    summary = str(entry.get("summary") or f"Waiting for {event_type}").strip()
    try:
        trigger_count = int(entry.get("trigger_count", 0) or 0)
    except (TypeError, ValueError):
        trigger_count = 0
    return {
        "id": str(entry.get("id") or ""),
        "name": name,
        "event_type": event_type,
        "enabled": bool(entry.get("enabled", True)),
        "trigger_count": trigger_count,
        "last_triggered_at": entry.get("last_triggered_at"),
        "last_run_status": str(entry.get("last_run_status") or "idle"),
        "last_payload": last_payload if isinstance(last_payload, dict) else None,
        "summary": summary,
        "logs": logs,
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at") or entry.get("created_at"),
    }


def _read_event_widget_records(state_reader: StateReader) -> dict[str, dict]:
    event_path = _event_widget_path(state_reader)
    raw = state_reader._read_json_from_path(event_path)  # noqa: SLF001 - shared helper
    if not isinstance(raw, dict):
        return {}
    return {
        entry_id: _normalize_event_widget_record({**entry, "id": entry.get("id") or entry_id})
        for entry_id, entry in raw.items()
        if isinstance(entry, dict)
    }


def _write_event_widget_records(
    state_reader: StateReader, records: dict[str, dict]
) -> dict[str, dict]:
    event_path = _event_widget_path(state_reader)
    normalized = {
        entry_id: _normalize_event_widget_record({**entry, "id": entry.get("id") or entry_id})
        for entry_id, entry in records.items()
        if isinstance(entry, dict)
    }
    _atomic_write_json(event_path, normalized, state_reader)
    state_reader.invalidate(event_path)
    return normalized


def _validate_event_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="Event type is required")
    if normalized not in KANBAN_EVENT_TYPE_LABELS:
        raise HTTPException(status_code=400, detail=f"Unsupported event type: {value}")
    return normalized


def _build_event_widget_entry(
    *,
    event_widget_id: str,
    name: str,
    event_type: str,
    enabled: bool,
    created_at: str,
) -> dict:
    normalized_type = _validate_event_type(event_type)
    return {
        "id": event_widget_id,
        "name": name,
        "event_type": normalized_type,
        "enabled": enabled,
        "trigger_count": 0,
        "last_triggered_at": None,
        "last_run_status": "idle",
        "last_payload": None,
        "summary": f"Waiting for {normalized_type}",
        "logs": [],
        "created_at": created_at,
        "updated_at": created_at,
    }


def _write_kanban_board(state_reader: StateReader, board: dict) -> dict:
    """Persist the kanban board as a structure document + per-card documents.

    Diff-aware: only cards whose payload actually changed are rewritten, and
    cards dropped from the board have their ``kanban_card:<id>`` row deleted.
    Each touched row fires the shared-store NOTIFY trigger, so a single card
    edit produces a single ``kanban.snapshot`` rebroadcast rather than a storm.
    """
    store = _require_document_store(state_reader)
    normalized = _normalize_kanban_board(board)
    normalized["updated_at"] = utcnow().isoformat()
    cards = normalized.get("cards", [])

    previous = store.get_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), None)
    previous_order = []
    if previous is not None and isinstance(previous.payload, dict):
        order = previous.payload.get("card_order")
        if isinstance(order, list):
            previous_order = [str(cid).strip() for cid in order if str(cid).strip()]

    card_order: list[str] = []
    for card in cards:
        card_id = str(card.get("id") or "").strip()
        if not card_id:
            continue
        card_order.append(card_id)
        existing = _read_kanban_card_doc(store, card_id)
        if existing != card:
            store.save_document(PROGENITOR_STATE_PREFIX, _kanban_card_store_key(card_id), card)

    for stale_id in previous_order:
        if stale_id not in card_order:
            store.delete_document(PROGENITOR_STATE_PREFIX, _kanban_card_store_key(stale_id))

    structure = {k: v for k, v in normalized.items() if k != "cards"}
    structure["card_order"] = card_order
    store.save_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), structure)
    return normalized


def _atomic_write_json(
    filepath: Path, data: object, state_reader: StateReader | None = None
) -> None:
    """Atomically write JSON for backend-backed state updates."""
    reader = state_reader or get_state_reader()
    if reader._write_json_to_path(filepath, data):  # noqa: SLF001 - shared persistence bridge
        reader.invalidate(filepath)
        return
    filepath.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=filepath.parent, prefix=f".{filepath.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


def _read_mcp_config(state_reader: StateReader) -> dict:
    """Read the live MCP config from the shared state document."""
    return state_reader.get_mcp_config()


def _write_mcp_config(state_reader: StateReader, data: dict[str, Any]) -> None:
    """Persist the MCP config to the shared state document."""
    state_reader.save_mcp_config(data)


def _read_schedule_config(state_reader: StateReader) -> tuple[Path, dict]:
    """Read the live schedule config, keyed by schedule id."""
    schedule_path = state_reader.schedule_config_path
    raw = state_reader._read_json_from_path(schedule_path)  # noqa: SLF001 - shared helper
    return schedule_path, raw if isinstance(raw, dict) else {}


def _delete_schedule_entry(state_reader: StateReader, schedule_id: str) -> bool:
    """Delete a persisted schedule entry and refresh the cached schedule file."""
    schedule_path, schedule_data = _read_schedule_config(state_reader)

    def _save() -> None:
        _atomic_write_json(schedule_path, schedule_data, state_reader)
        state_reader.invalidate(schedule_path)

    return _delete_document_entry(entries=schedule_data, entry_id=schedule_id, save=_save)


def _broadcast_widget_refresh(state_reader) -> None:
    """Notify status listeners that backend-derived widgets changed."""
    broadcast_status_event("widgets", {"items": state_reader.get_widgets()})


def _find_widget_payload(state_reader: StateReader, widget_id: str) -> dict[str, Any]:
    widget = next(
        (item for item in state_reader.get_widgets() if str(item.get("id")) == widget_id),
        None,
    )
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    return dict(widget)


def _format_httpx_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip()
        if len(body) > 240:
            body = f"{body[:237]}..."
        return (
            f"{exc.request.method} {exc.request.url} returned "
            f"{exc.response.status_code}: {body or exc.response.reason_phrase}"
        )
    if isinstance(exc, httpx.RequestError):
        return f"Request to {exc.request.url} failed: {exc}"
    return str(exc)


def _agent_card_url(agent: Any) -> str | None:
    direct = str(getattr(agent, "agent_card_url", "") or "").strip()
    if direct:
        return direct

    base_url = str(getattr(agent, "url", "") or "").strip().rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/.well-known/agent-card.json"


async def _inspect_agent_widget(
    widget: dict[str, Any], state_reader: StateReader
) -> dict[str, Any]:
    agent_name = str(widget.get("name") or "").strip()
    agents, _ = state_reader.get_registry()
    agent = next((entry for entry in agents if entry.name == agent_name and entry.enabled), None)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent widget is not registered")

    card_url = _agent_card_url(agent)
    if card_url is None:
        raise HTTPException(status_code=400, detail="Agent is missing an agent-card URL")

    headers = {"Accept": "application/json"}
    if agent.headers:
        headers.update(agent.headers)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=headers) as client:
            response = await client.get(card_url)
            response.raise_for_status()
            payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent card returned invalid JSON: {exc}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_format_httpx_error(exc)) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Agent card response must be a JSON object")

    default_input_modes = payload.get("defaultInputModes")
    default_output_modes = payload.get("defaultOutputModes")
    skills = payload.get("skills")
    inspection = {
        "source": "a2a",
        "refreshed_at": utcnow().isoformat(),
        "card_url": card_url,
        "protocol_version": payload.get("protocolVersion"),
        "preferred_transport": payload.get("preferredTransport"),
        "default_input_modes": default_input_modes if isinstance(default_input_modes, list) else [],
        "default_output_modes": default_output_modes
        if isinstance(default_output_modes, list)
        else [],
        "capabilities": payload.get("capabilities"),
        "skills": skills if isinstance(skills, list) else [],
        "card": payload,
    }

    return {
        **widget,
        "description": str(payload.get("description") or widget.get("description") or ""),
        "summary": str(widget.get("summary") or payload.get("description") or ""),
        "url": str(payload.get("url") or getattr(agent, "url", "") or ""),
        "agent_card_url": card_url,
        "version": str(payload.get("version") or getattr(agent, "version", "") or ""),
        "availability_status": "online",
        "inspection": inspection,
    }


def _summarize_event_trigger(event_type: str, payload: dict) -> str:
    label = KANBAN_EVENT_TYPE_LABELS.get(event_type, event_type)
    card = payload.get("card") if isinstance(payload, dict) else None
    if isinstance(card, dict):
        title = str(card.get("title") or "").strip()
        if title:
            return f"{label} fired for {title}"
    return f"{label} fired"


def _apply_event_widget_triggers(state_reader: StateReader, triggers: list[dict]) -> None:
    if not triggers:
        return

    event_widgets = _read_event_widget_records(state_reader)
    if not event_widgets:
        return

    changed = False
    for widget in event_widgets.values():
        if not widget.get("enabled", True):
            continue

        for trigger in triggers:
            event_type = trigger.get("event_type")
            if not isinstance(event_type, str) or widget.get("event_type") != event_type:
                continue

            timestamp = str(trigger.get("timestamp") or utcnow().isoformat())
            payload = trigger.get("payload")
            normalized_payload = payload if isinstance(payload, dict) else {}
            summary = str(
                trigger.get("summary") or _summarize_event_trigger(event_type, normalized_payload)
            )
            widget["trigger_count"] = int(widget.get("trigger_count", 0) or 0) + 1
            widget["last_triggered_at"] = timestamp
            widget["last_run_status"] = "success"
            widget["last_payload"] = normalized_payload
            widget["summary"] = summary
            widget["updated_at"] = timestamp
            widget["logs"] = [
                {
                    "time": timestamp,
                    "timestamp": timestamp,
                    "level": "info",
                    "source": "kanban",
                    "message": summary,
                },
                *_normalize_event_widget_logs(widget.get("logs")),
            ][:MAX_EVENT_WIDGET_LOGS]
            changed = True

    if changed:
        _write_event_widget_records(state_reader, event_widgets)
        _broadcast_widget_refresh(state_reader)


async def _dispatch_workflow_triggers(triggers: list[dict]) -> None:
    if not triggers:
        return
    await get_workflow_executor().dispatch_triggers(triggers)


def _parse_once_datetime(expression: str) -> datetime:
    try:
        return datetime.fromisoformat(expression.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="One-off schedules must use an ISO datetime like 2026-03-15T14:30:00Z",
        ) from exc


def _interval_to_cron(expression: str) -> str:
    match = re.fullmatch(
        r"\s*(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*",
        expression,
        flags=re.IGNORECASE,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Interval schedules must look like 5m, 2h, or 1d",
        )

    value = int(match.group(1))
    unit = match.group(2).lower()
    if value <= 0:
        raise HTTPException(status_code=400, detail="Interval schedules must be greater than zero")

    if unit in {"s", "sec", "secs", "second", "seconds"}:
        if value % 60 != 0:
            raise HTTPException(
                status_code=400,
                detail="Second-based intervals must be divisible by 60",
            )
        value //= 60
        unit = "minute"

    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return f"*/{value} * * * *"
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return f"0 */{value} * * *"
    return f"0 0 */{value} * *"


def _resolve_schedule_expression(
    schedule_type: str,
    expression: str,
) -> tuple[str, str | None, str | None]:
    normalized_type = (schedule_type or "cron").strip().lower()
    normalized_expression = expression.strip()
    if not normalized_expression:
        raise HTTPException(status_code=400, detail="Schedule expression is required")

    if normalized_type == "cron":
        try:
            croniter(normalized_expression, utcnow())
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid cron expression") from exc
        return normalized_type, normalized_expression, None

    if normalized_type == "interval":
        return normalized_type, _interval_to_cron(normalized_expression), None

    if normalized_type == "once":
        run_at = _parse_once_datetime(normalized_expression)
        return normalized_type, None, run_at.isoformat()

    raise HTTPException(status_code=400, detail=f"Unsupported schedule type: {schedule_type}")


def _build_schedule_entry(
    *,
    schedule_id: str,
    name: str,
    instruction: str,
    schedule_type: str,
    expression: str,
    timezone_name: str | None,
    enabled: bool,
    created_at: str,
) -> dict:
    normalized_type, cron_expression, scheduled_at = _resolve_schedule_expression(
        schedule_type,
        expression,
    )
    entry = {
        "id": schedule_id,
        "name": name,
        "message": instruction,
        "instruction": instruction,
        "user_request": name,
        "schedule": expression,
        "schedule_type": normalized_type,
        "timezone": timezone_name or default_timezone_name(),
        "enabled": enabled,
        # The runtime scheduler only honours ``paused``; keep it in sync with the
        # UI's ``enabled`` flag so a paused schedule actually stops firing.
        "paused": not enabled,
        "last_run": None,
        "last_run_status": "idle",
        "created_at": created_at,
        "updated_at": created_at,
    }
    if cron_expression is not None:
        entry["cron_expression"] = cron_expression
    if scheduled_at is not None:
        entry["scheduled_at"] = scheduled_at
    next_run = Scheduler().next_run_for_entry(entry)
    if next_run:
        entry["next_run"] = next_run.isoformat()
        entry["next_run_at"] = next_run.isoformat()
    return entry


@router.get("/workspace", response_model=WorkspaceResponse)
async def get_workspace():
    """Get browser-workspace bootstrap plus backend-derived widgets."""
    state_reader = get_state_reader()
    payload = state_reader.get_workspace_payload()
    return WorkspaceResponse(
        timestamp=payload["timestamp"],
        workspace=payload["workspace"],
        health=payload["health"],
        sessions=payload["sessions"],
        widgets=[_widget_to_response(widget) for widget in payload["widgets"]],
    )


def _workflow_content_response(definition: WorkflowDefinition) -> WorkflowContentResponse:
    return WorkflowContentResponse(
        path=f"/state/workflows/{definition.id}.json",
        workflow=definition,
    )


def _workflow_event_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return _workflow_content_response(definition).model_dump(mode="json", by_alias=True)


@router.get("/workflows", response_model=WorkflowsResponse)
async def get_workflows():
    """Return the workflow definition list for the workflows page/sidebar."""

    items = list_workflow_list_items(get_default_document_store())
    broadcast_ui_event(
        "workflows.snapshot",
        {"items": [item.model_dump(mode="json") for item in items]},
    )
    return WorkflowsResponse(items=items)


@router.get("/workflows/content", response_model=WorkflowContentResponse)
async def get_workflow_content(path: str = Query(...)):
    """Return one stored workflow definition by id or mounted virtual path."""

    try:
        definition = get_workflow_definition(get_default_document_store(), path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    payload = _workflow_content_response(definition)
    broadcast_ui_event("workflow.updated", payload.model_dump(mode="json", by_alias=True))
    return payload


@router.put("/workflows/content", response_model=WorkflowContentResponse)
async def put_workflow_content(body: WorkflowContentPutRequest):
    """Persist one workflow definition."""

    workflow_payload = dict(body.workflow)
    target_path = body.path
    if target_path:
        try:
            workflow_id = workflow_id_from_path(target_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if workflow_payload.get("id") and workflow_payload.get("id") != workflow_id:
            raise HTTPException(status_code=400, detail="Workflow id must match requested path")
        workflow_payload["id"] = workflow_id
    try:
        definition = save_workflow_definition(get_default_document_store(), workflow_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _workflow_content_response(definition)
    broadcast_ui_event("workflow.updated", payload.model_dump(mode="json", by_alias=True))
    broadcast_ui_event(
        "workflows.snapshot",
        {
            "items": [
                item.model_dump(mode="json")
                for item in list_workflow_list_items(get_default_document_store())
            ]
        },
    )
    return payload


@router.post("/workflows", response_model=WorkflowContentResponse)
async def create_workflow(body: WorkflowCreateRequest):
    """Create a new workflow definition with the minimal valid scaffold."""

    if body.event_type not in WORKFLOW_ALLOWED_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported workflow event type")
    try:
        definition = save_workflow_definition(
            get_default_document_store(),
            default_workflow_definition(
                title=body.title.strip() or "Untitled workflow",
                description=body.description,
                event_type=body.event_type,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _workflow_content_response(definition)
    broadcast_ui_event("workflow.updated", payload.model_dump(mode="json", by_alias=True))
    broadcast_ui_event(
        "workflows.snapshot",
        {
            "items": [
                item.model_dump(mode="json")
                for item in list_workflow_list_items(get_default_document_store())
            ]
        },
    )
    return payload


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str):
    """Delete one stored workflow definition."""

    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    delete_workflow_definition(store, workflow_id)
    broadcast_ui_event("workflow.deleted", {"workflow_id": workflow_id})
    broadcast_ui_event(
        "workflows.snapshot",
        {"items": [item.model_dump(mode="json") for item in list_workflow_list_items(store)]},
    )
    return None


@router.post("/workflows/{workflow_id}/pause", response_model=WorkflowContentResponse)
async def pause_workflow(workflow_id: str):
    """Pause one workflow definition and any active run."""

    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    updated = save_workflow_definition(
        store,
        definition.model_copy(
            update={"paused": True, "updated_at": utcnow().isoformat()}
        ).model_dump(mode="json", by_alias=True),
    )
    await get_workflow_executor().pause_workflow(updated.id)
    payload = _workflow_content_response(updated)
    broadcast_ui_event("workflow.updated", payload.model_dump(mode="json", by_alias=True))
    broadcast_workflows_snapshot()
    return payload


@router.post("/workflows/{workflow_id}/resume", response_model=WorkflowContentResponse)
async def resume_workflow(workflow_id: str):
    """Resume one paused workflow definition and its active run, if any."""

    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    updated = save_workflow_definition(
        store,
        definition.model_copy(
            update={"paused": False, "updated_at": utcnow().isoformat()}
        ).model_dump(mode="json", by_alias=True),
    )
    await get_workflow_executor().resume_workflow(updated.id)
    payload = _workflow_content_response(updated)
    broadcast_ui_event("workflow.updated", payload.model_dump(mode="json", by_alias=True))
    broadcast_workflows_snapshot()
    return payload


@router.get("/workflow-runs", response_model=WorkflowRunsResponse)
async def get_workflow_runs():
    """Return active and recent workflow runs."""

    items = list_workflow_runs(get_default_document_store())
    broadcast_workflow_runs_snapshot(items)
    return WorkflowRunsResponse(items=items)


@router.post("/workflows/{workflow_id}/trigger", response_model=WorkflowTriggerResponse)
async def trigger_workflow(workflow_id: str, body: WorkflowManualTriggerRequest):
    """Manually trigger one workflow definition for operator testing."""

    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        trigger = build_manual_trigger_event(
            definition,
            event_type=body.event_type,
            payload=body.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    active_before = find_active_workflow_run(store, definition.id)
    run = await get_workflow_executor().trigger_workflow(definition.id, trigger)
    if run is None:
        # Executor refused (workflow paused or disabled) — not queued
        refreshed = (
            get_workflow_run(store, active_before.run_id) if active_before is not None else None
        )
        return WorkflowTriggerResponse(ok=False, queued=False, run=refreshed or active_before)
    queued = active_before is not None and run.run_id == active_before.run_id
    if queued:
        return WorkflowTriggerResponse(ok=True, queued=True, run=active_before)
    refreshed = get_workflow_run(store, run.run_id) if run is not None else None
    return WorkflowTriggerResponse(ok=True, queued=False, run=refreshed or run)


@router.get("/database/schema")
async def get_database_schema():
    """Return safe table/column metadata for the configured backend document store."""
    return {"tables": get_default_document_store().inspect_schema()}


def _mcp_widget_items() -> list[WidgetResponse]:
    """The MCP-type widgets, used by the /mcp route and the mcp.snapshot event."""
    state_reader = get_state_reader()
    return [
        _widget_to_response(widget)
        for widget in state_reader.get_widgets()
        if widget.get("type") == "mcp"
    ]


@router.get("/widgets", response_model=WidgetsResponse)
async def get_widgets():
    """Return the unified widget list and publish it on the UI stream.

    Mirrors the schedule/agent/mcp pattern: the GET acknowledges and broadcasts
    ``widgets.updated`` so pages render from the realtime store rather than
    holding the HTTP response themselves.
    """
    state_reader = get_state_reader()
    widgets = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    broadcast_widgets_snapshot(widgets)
    return WidgetsResponse(items=widgets, total=len(widgets))


@router.get("/mcp", response_model=WidgetsResponse)
async def get_mcp_servers():
    """Acknowledge MCP-list requests and publish the snapshot on the UI stream.

    Mirrors the schedule/agent pattern: a dedicated typed event (``mcp.snapshot``)
    feeds a dedicated store slice, so the Tools page renders from the realtime
    store instead of filtering the generic widget list.
    """
    items = _mcp_widget_items()
    broadcast_mcp_snapshot(items)
    return WidgetsResponse(items=items, total=len(items))


@router.get("/widget-schemas", response_model=WidgetSchemasResponse)
async def get_widget_schemas():
    """Get schema metadata for the workspace details pane."""
    return WidgetSchemasResponse(schemas=_widget_schemas())


@router.post("/widgets/{widget_id}/refresh", response_model=WidgetResponse)
async def refresh_widget(widget_id: str):
    """Refresh agent widget metadata from its live protocol surface.

    MCP servers are not refreshed here: their tools/capabilities are discovered
    by the Progenitor runtime when it initialises its MCP context (and on
    ``mcp_refresh``), which is the single source of truth the UI reads from.
    """
    state_reader = get_state_reader()
    widget = _find_widget_payload(state_reader, widget_id)

    if widget.get("type") == "agent":
        refreshed = await _inspect_agent_widget(widget, state_reader)
        return _widget_to_response(refreshed)

    raise HTTPException(
        status_code=400,
        detail="Only agent widgets support live refresh",
    )


def _patch_document_entry(
    *,
    entries: dict,
    entry_id: str,
    apply: Callable[[dict], None],
    save: Callable[[], None],
    not_found_detail: str = "Widget not found",
) -> dict:
    """Mutate one dict-keyed entry in place, persist it, and return it.

    ``entries`` is the already-read whole document (or the id-keyed
    sub-document, e.g. mcp's ``mcpServers``); ``save`` persists whatever
    container ``entries`` belongs to - the field-level mutation itself stays a
    per-widget-type closure, since that logic genuinely differs per type.
    """
    entry = entries.get(entry_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=not_found_detail)
    apply(entry)
    save()
    return entry


def _delete_document_entry(*, entries: dict, entry_id: str, save: Callable[[], None]) -> bool:
    """Delete one dict-keyed entry if present, persist, and report whether it existed."""
    if not isinstance(entries.get(entry_id), dict):
        return False
    del entries[entry_id]
    save()
    return True


def _widget_or_fallback(
    widget_id: str, state_reader: StateReader, build_fallback: Callable[[], dict]
) -> dict:
    """Return the freshly-computed widget if ``get_widgets()`` already reflects the
    write, else a caller-supplied synthetic stand-in (cache staleness fallback)."""
    refreshed = next((w for w in state_reader.get_widgets() if w["id"] == widget_id), None)
    return refreshed if refreshed is not None else build_fallback()


@router.patch("/widgets/{widget_id}", response_model=WidgetResponse)
async def patch_widget(widget_id: str, body: WidgetPatchRequest):
    """Patch a backend-owned widget where a matching state file exists."""
    state_reader = get_state_reader()
    if widget_id.startswith("schedule:"):
        schedule_id = widget_id.split(":", 1)[1]
        schedule_path, schedule_data = _read_schedule_config(state_reader)

        def _apply_schedule_patch(entry: dict) -> None:
            current_schedule_type = body.schedule_type or entry.get("schedule_type") or "cron"
            current_expression = (
                body.expression or entry.get("schedule") or entry.get("cron_expression") or ""
            )
            if body.enabled is not None:
                entry["enabled"] = body.enabled
                # Mirror into ``paused`` so the runtime scheduler honours the change.
                entry["paused"] = not body.enabled
            if body.name is not None:
                entry["name"] = body.name
                entry["user_request"] = body.name
            if body.instruction is not None:
                entry["message"] = body.instruction
                entry["instruction"] = body.instruction
            if body.expression is not None or body.schedule_type is not None:
                normalized_type, cron_expression, scheduled_at = _resolve_schedule_expression(
                    current_schedule_type,
                    current_expression,
                )
                entry["schedule_type"] = normalized_type
                entry["schedule"] = current_expression
                if cron_expression is None:
                    entry.pop("cron_expression", None)
                else:
                    entry["cron_expression"] = cron_expression
                if scheduled_at is None:
                    entry.pop("scheduled_at", None)
                else:
                    entry["scheduled_at"] = scheduled_at
            if body.timezone is not None:
                entry["timezone"] = body.timezone
            entry["updated_at"] = utcnow().isoformat()
            next_run = Scheduler().next_run_for_entry(entry)
            entry["next_run"] = next_run.isoformat() if next_run else None
            entry["next_run_at"] = next_run.isoformat() if next_run else None

        def _save_schedule() -> None:
            _atomic_write_json(schedule_path, schedule_data, state_reader)
            state_reader.invalidate(schedule_path)

        updated = _patch_document_entry(
            entries=schedule_data,
            entry_id=schedule_id,
            apply=_apply_schedule_patch,
            save=_save_schedule,
        )

        refreshed = _widget_or_fallback(
            widget_id,
            state_reader,
            lambda: {
                "id": widget_id,
                "type": "schedule",
                "name": updated.get("name", schedule_id),
                "title": updated.get("name", schedule_id),
                "status": "enabled" if updated.get("enabled", True) else "disabled",
                "description": updated.get("message"),
                "summary": updated.get("message"),
                "logs": [],
                "updated_at": updated.get("created_at"),
            },
        )

        _broadcast_widget_refresh(state_reader)
        broadcast_schedule_snapshot()
        return _widget_to_response(refreshed)

    if widget_id.startswith("event:"):
        event_id = widget_id.split(":", 1)[1]
        event_widgets = _read_event_widget_records(state_reader)

        def _apply_event_patch(entry: dict) -> None:
            if body.enabled is not None:
                entry["enabled"] = body.enabled
            if body.name is not None:
                name = body.name.strip()
                if not name:
                    raise HTTPException(status_code=400, detail="Event widget name is required")
                entry["name"] = name
            if body.event_type is not None:
                entry["event_type"] = _validate_event_type(body.event_type)
                if not entry.get("summary") or str(entry.get("summary")).startswith("Waiting for "):
                    entry["summary"] = f"Waiting for {entry['event_type']}"
            entry["updated_at"] = utcnow().isoformat()

        def _save_event_widgets() -> None:
            _write_event_widget_records(state_reader, event_widgets)

        updated = _patch_document_entry(
            entries=event_widgets,
            entry_id=event_id,
            apply=_apply_event_patch,
            save=_save_event_widgets,
        )

        refreshed = _widget_or_fallback(
            widget_id,
            state_reader,
            lambda: {
                "id": widget_id,
                "type": "event",
                "name": updated.get("name", event_id),
                "title": updated.get("name", event_id),
                "status": "enabled" if updated.get("enabled", True) else "disabled",
                "description": updated.get("event_type"),
                "summary": updated.get("summary"),
                "logs": updated.get("logs", []),
                "updated_at": updated.get("updated_at"),
                "event_type": updated.get("event_type"),
                "enabled": updated.get("enabled", True),
                "trigger_count": updated.get("trigger_count", 0),
                "last_triggered_at": updated.get("last_triggered_at"),
                "last_run_status": updated.get("last_run_status", "idle"),
                "last_payload": updated.get("last_payload"),
            },
        )

        _broadcast_widget_refresh(state_reader)
        return _widget_to_response(refreshed)

    if widget_id.startswith("mcp_tool:"):
        tool_name = widget_id.split(":", 1)[1]
        if body.enabled is None:
            raise HTTPException(status_code=400, detail="enabled is required for MCP tool widgets")
        tools_doc = state_reader.get_mcp_tools()
        raw_tools = tools_doc.get("tools") if isinstance(tools_doc, dict) else None
        target = (
            next(
                (t for t in raw_tools if isinstance(t, dict) and t.get("name") == tool_name),
                None,
            )
            if isinstance(raw_tools, list)
            else None
        )
        if target is None:
            raise HTTPException(status_code=404, detail="MCP tool not found")
        target["enabled"] = body.enabled
        # The runtime owns the tool list; we only flip the enabled flag here. The
        # runtime re-reads this document on its next MCP refresh and stops/starts
        # exposing the tool to the agent accordingly.
        state_reader.save_mcp_tools(tools_doc)
        _broadcast_widget_refresh(state_reader)
        return _widget_to_response(
            {
                "id": widget_id,
                "type": "mcp_tool",
                "name": tool_name,
                "title": tool_name,
                "status": "enabled" if body.enabled else "disabled",
                "enabled": body.enabled,
                "server": target.get("server"),
                "description": target.get("description"),
            }
        )

    if not widget_id.startswith("mcp:"):
        raise HTTPException(
            status_code=400,
            detail="Only schedule, event, MCP, and MCP tool widgets are mutable from the backend",
        )

    server_name = widget_id.split(":", 1)[1]
    mcp_data = _read_mcp_config(state_reader)
    raw_servers = mcp_data.get("mcpServers")
    if not isinstance(raw_servers, dict):
        raw_servers = {}
        mcp_data["mcpServers"] = raw_servers

    def _apply_mcp_patch(server: dict) -> None:
        requested_name = (body.name or "").strip()
        if requested_name and requested_name != server_name:
            raise HTTPException(status_code=400, detail="Renaming MCP servers is not supported")
        if body.enabled is not None:
            server["enabled"] = body.enabled
        if body.description is not None:
            server["description"] = body.description
        if body.url is not None:
            url = body.url.strip()
            if not url:
                raise HTTPException(status_code=400, detail="MCP server URL is required")
            server["url"] = url

    server = _patch_document_entry(
        entries=raw_servers,
        entry_id=server_name,
        apply=_apply_mcp_patch,
        save=lambda: _write_mcp_config(state_reader, mcp_data),
    )

    refreshed = _widget_or_fallback(
        widget_id,
        state_reader,
        lambda: {
            "id": widget_id,
            "type": "mcp",
            "name": server_name,
            "title": server_name,
            "description": str(server.get("description") or ""),
            "status": "enabled" if bool(server.get("enabled", True)) else "disabled",
            "summary": str(server.get("description") or "") or f"MCP {server_name}",
            "enabled": bool(server.get("enabled", True)),
            "url": server.get("url"),
            "transport": server.get("transport", "http"),
            "logs": [],
            "updated_at": None,
        },
    )

    _broadcast_widget_refresh(state_reader)
    return _widget_to_response(refreshed)


@router.delete("/widgets/{widget_id}", response_model=WidgetDeleteResponse)
async def delete_widget(widget_id: str):
    """Delete a backend-owned widget where a matching state file exists."""
    state_reader = get_state_reader()

    if widget_id.startswith("mcp:"):
        server_name = widget_id.split(":", 1)[1]
        mcp_data = _read_mcp_config(state_reader)
        raw_servers = mcp_data.get("mcpServers")
        if not isinstance(raw_servers, dict):
            raw_servers = {}
        deleted = _delete_document_entry(
            entries=raw_servers,
            entry_id=server_name,
            save=lambda: _write_mcp_config(state_reader, mcp_data),
        )
        if not deleted:
            warning = f"Widget {widget_id} was already removed."
            logger.warning("MCP widget %s was already removed before delete request", widget_id)
            return WidgetDeleteResponse(
                widget_id=widget_id,
                removed=True,
                already_removed=True,
                warning=warning,
            )
        _broadcast_widget_refresh(state_reader)
        return WidgetDeleteResponse(widget_id=widget_id, removed=True)

    if not widget_id.startswith("schedule:"):
        raise HTTPException(
            status_code=400,
            detail="Only schedule and MCP widgets can be deleted from the backend",
        )

    schedule_id = widget_id.split(":", 1)[1]
    deleted = _delete_schedule_entry(state_reader, schedule_id)
    if not deleted:
        warning = f"Widget {widget_id} was already removed."
        logger.warning("Schedule widget %s was already removed before delete request", widget_id)
        return WidgetDeleteResponse(
            widget_id=widget_id,
            removed=True,
            already_removed=True,
            warning=warning,
        )

    _broadcast_widget_refresh(state_reader)
    broadcast_schedule_snapshot()
    return WidgetDeleteResponse(widget_id=widget_id, removed=True)


@router.get("/schedules", response_model=ScheduleResponse)
async def get_schedules():
    """Alias for schedule data using the requested plural path."""
    return await get_schedule()


@router.post("/schedules", response_model=WidgetResponse)
async def create_schedule(body: ScheduleCreateRequest):
    """Create a new schedule entry in backend state."""
    name = body.name.strip()
    instruction = body.instruction.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Schedule name is required")
    if not instruction:
        raise HTTPException(status_code=400, detail="Schedule task is required")

    state_reader = get_state_reader()
    schedule_path, schedule_data = _read_schedule_config(state_reader)

    created_at = utcnow().isoformat()
    schedule_id = generate_schedule_id(is_one_off=body.schedule_type.strip().lower() == "once")
    entry = _build_schedule_entry(
        schedule_id=schedule_id,
        name=name,
        instruction=instruction,
        schedule_type=body.schedule_type,
        expression=body.expression,
        timezone_name=body.timezone,
        enabled=body.enabled,
        created_at=created_at,
    )
    schedule_data[schedule_id] = entry

    _atomic_write_json(schedule_path, schedule_data, state_reader)
    state_reader.invalidate(schedule_path)

    refreshed = next(
        (
            widget
            for widget in state_reader.get_widgets()
            if widget["id"] == f"schedule:{schedule_id}"
        ),
        None,
    )
    if refreshed is None:
        refreshed = {
            "id": f"schedule:{schedule_id}",
            "type": "schedule",
            "name": name,
            "title": name,
            "status": "enabled" if body.enabled else "disabled",
            "description": name,
            "summary": instruction,
            "updated_at": created_at,
            "data": entry,
        }

    _broadcast_widget_refresh(state_reader)
    broadcast_schedule_snapshot()
    return _widget_to_response(refreshed)


@router.post("/schedules/{schedule_id}/trigger", response_model=WidgetResponse)
async def retrigger_schedule(schedule_id: str, body: ScheduleTriggerRequest):
    """Reschedule a task from a natural-language description.

    Rather than editing cron fields by hand, the user describes the timing in
    plain language ("friday at 15:00"). We hand that to the Progenitor agent on a
    dedicated (non-chat) thread carrying just the schedule id and the free text -
    the loaded ``managing-schedules`` skill owns how a natural-language reschedule request
    gets interpreted and applied to ``/state/schedule.json``, not a hardcoded
    tool-call prescription here. We then recompute ``next_run`` (in case the
    agent didn't) and rebroadcast a snapshot.
    """
    from . import main as chat_main

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Trigger description is required")
    if not chat_main.client:
        raise HTTPException(status_code=503, detail="Progenitor client not initialized")

    state_reader = get_state_reader()
    schedule_path, schedule_data = _read_schedule_config(state_reader)
    entry = schedule_data.get(schedule_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    instruction = (
        f'Reschedule the task with id "{schedule_id}" under /state/schedule.json to run: '
        f'"{text}". Do not change the task\'s message and do not create a new task. Reply '
        "with one short sentence confirming the new timing."
    )

    thread_id = _resolve_action_thread_id(None)
    metadata = _build_action_metadata(
        thread_id=thread_id,
        invocation_type="system",
        invocation_source="schedule.trigger",
        widget_id=f"schedule:{schedule_id}",
        root_widget_id=f"schedule:{schedule_id}",
        action="schedule.trigger",
    )
    upsert_thread(
        thread_id,
        invocation_type="system",
        invocation_source="schedule.trigger",
        root_widget_id=f"schedule:{schedule_id}",
        widget_id=f"schedule:{schedule_id}",
    )
    try:
        await chat_main.client.send_message(instruction, metadata=metadata)
    except Exception as exc:
        fail_thread(thread_id, str(exc), widget_id=f"schedule:{schedule_id}")
        raise HTTPException(status_code=502, detail=f"Agent reschedule failed: {exc}") from exc
    complete_thread(thread_id, "schedule updated", widget_id=f"schedule:{schedule_id}")

    # The agent edits cron_expression/scheduled_at directly but may not recompute
    # next_run or the display fields; reconcile them here so the UI stays coherent.
    state_reader.invalidate(schedule_path)
    _, schedule_data = _read_schedule_config(state_reader)
    updated = schedule_data.get(schedule_id)
    if updated is not None:
        updated["updated_at"] = utcnow().isoformat()
        if updated.get("scheduled_at"):
            updated["schedule_type"] = "once"
            updated["schedule"] = updated["scheduled_at"]
            updated.pop("cron_expression", None)
        elif updated.get("cron_expression"):
            updated["schedule_type"] = "cron"
            updated["schedule"] = updated["cron_expression"]
        next_run = Scheduler().next_run_for_entry(updated)
        updated["next_run"] = next_run.isoformat() if next_run else None
        updated["next_run_at"] = next_run.isoformat() if next_run else None
        _atomic_write_json(schedule_path, schedule_data, state_reader)
        state_reader.invalidate(schedule_path)

    _broadcast_widget_refresh(state_reader)
    broadcast_schedule_snapshot()
    refreshed = next(
        (w for w in state_reader.get_widgets() if w["id"] == f"schedule:{schedule_id}"),
        None,
    )
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return _widget_to_response(refreshed)


@router.post("/events", response_model=WidgetResponse)
async def create_event_widget(body: EventWidgetCreateRequest):
    """Create a new persisted event widget trigger."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Event widget name is required")

    state_reader = get_state_reader()
    event_widgets = _read_event_widget_records(state_reader)
    created_at = utcnow().isoformat()
    event_widget_id = f"evt_{uuid.uuid4().hex[:12]}"
    entry = _build_event_widget_entry(
        event_widget_id=event_widget_id,
        name=name,
        event_type=body.event_type,
        enabled=body.enabled,
        created_at=created_at,
    )
    event_widgets[event_widget_id] = entry
    _write_event_widget_records(state_reader, event_widgets)

    refreshed = next(
        (
            widget
            for widget in state_reader.get_widgets()
            if widget["id"] == f"event:{event_widget_id}"
        ),
        None,
    )
    if refreshed is None:
        refreshed = {
            "id": f"event:{event_widget_id}",
            "type": "event",
            "name": name,
            "title": name,
            "status": "enabled" if body.enabled else "disabled",
            "description": entry["event_type"],
            "summary": entry["summary"],
            "updated_at": created_at,
            "data": entry,
        }

    _broadcast_widget_refresh(state_reader)
    return _widget_to_response(refreshed)


@router.get("/kanban", response_model=KanbanBoardResponse)
async def get_kanban_board():
    """Get the persisted kanban board used by the frontend."""
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    response = _kanban_board_response(state_reader, board)
    broadcast_kanban_snapshot(response)
    return response


@router.post("/kanban/cards", response_model=KanbanCardResponse)
async def create_kanban_card(body: KanbanCardCreateRequest):
    """Create a new kanban card in the backlog column."""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Card title is required")

    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    column_map = {column["id"]: column for column in board["columns"]}
    now = utcnow().isoformat()
    card = _normalize_kanban_card(
        {
            "id": f"card_{uuid.uuid4().hex[:12]}",
            "title": title,
            "summary": body.summary,
            "description": body.description,
            "column_id": "backlog",
            "status": "draft",
            "agent_name": column_map["backlog"]["agent_name"],
            "owner": body.owner,
            "project_id": body.project_id,
            "labels": body.labels,
            "created_at": now,
            "updated_at": now,
            "position": sum(1 for item in board["cards"] if item["column_id"] == "backlog"),
        },
        column_map,
    )
    board["cards"].append(card)
    board = _write_kanban_board(state_reader, board)
    created = next((item for item in board["cards"] if item["id"] == card["id"]), card)
    _apply_event_widget_triggers(
        state_reader,
        [
            {
                "event_type": "kanban.card.created",
                "timestamp": now,
                "payload": {
                    "event_type": "kanban.card.created",
                    "card": created,
                },
            }
        ],
    )
    await _dispatch_workflow_triggers(
        [
            {
                "event_type": "kanban.card.created",
                "timestamp": now,
                "payload": {
                    "event_type": "kanban.card.created",
                    "card": created,
                    "timestamp": now,
                    "source": "kanban",
                },
            }
        ]
    )
    async with _kanban_card_trigger_lock:
        _remember_kanban_card_state(created["id"], created)
    response = _kanban_card_response(state_reader, created)
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board))
    return response


@router.patch("/kanban/cards/{card_id}", response_model=KanbanCardResponse)
async def patch_kanban_card(card_id: str, body: KanbanCardPatchRequest):
    """Patch an existing kanban card."""
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    column_map = {column["id"]: column for column in board["columns"]}
    allowed_statuses = {status.id for status in _kanban_status_options()}
    now = utcnow().isoformat()

    updated_card = None
    previous_card = None
    for card in board["cards"]:
        if card["id"] != card_id:
            continue

        previous_card = json.loads(json.dumps(card))
        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="Card title is required")
            card["title"] = title
        if body.summary is not None:
            card["summary"] = body.summary.strip()
        if body.description is not None:
            card["description"] = body.description
        if body.column_id is not None:
            if body.column_id not in column_map:
                raise HTTPException(
                    status_code=400, detail=f"Unknown kanban column: {body.column_id}"
                )
            card["column_id"] = body.column_id
            if body.agent_name is None:
                card["agent_name"] = column_map[body.column_id]["agent_name"]
        if body.status is not None:
            status = body.status.strip().lower()
            if status not in allowed_statuses:
                raise HTTPException(status_code=400, detail=f"Unknown kanban status: {body.status}")
            card["status"] = status
            if status == "running" and not card.get("started_at"):
                card["started_at"] = now
            if status == "complete":
                card["completed_at"] = body.completed_at or card.get("completed_at") or now
        if body.agent_name is not None:
            card["agent_name"] = body.agent_name.strip()
        if body.owner is not None:
            card["owner"] = body.owner.strip() or "user"
        if body.project_id is not None:
            card["project_id"] = body.project_id.strip() or None
        if body.labels is not None:
            card["labels"] = _normalize_string_list(body.labels)
        if body.spec_path is not None:
            card["spec_path"] = body.spec_path.strip() or None
        if body.branch_name is not None:
            card["branch_name"] = body.branch_name.strip() or None
        if body.pr_url is not None:
            card["pr_url"] = body.pr_url.strip() or None
        if body.started_at is not None:
            card["started_at"] = body.started_at.strip() or None
        if body.completed_at is not None:
            card["completed_at"] = body.completed_at.strip() or None
        if body.last_error is not None:
            card["last_error"] = body.last_error.strip() or None
        if body.metadata is not None:
            card["metadata"] = body.metadata if isinstance(body.metadata, dict) else {}

        card["updated_at"] = now
        updated_card = _normalize_kanban_card(card, column_map)
        card.update(updated_card)
        break

    if updated_card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    target_position = body.position if body.position is not None else updated_card["position"]
    board["cards"] = _reorder_kanban_cards(
        board["cards"],
        board["columns"],
        card_id,
        updated_card["column_id"],
        target_position,
    )
    board = _write_kanban_board(state_reader, board)
    refreshed = next((item for item in board["cards"] if item["id"] == card_id), updated_card)
    if previous_card is not None:
        await _apply_kanban_card_diff_effects(
            state_reader, previous_card=previous_card, refreshed_card=refreshed, now=now
        )
    async with _kanban_card_trigger_lock:
        _remember_kanban_card_state(card_id, refreshed)
    response = _kanban_card_response(state_reader, refreshed)
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board))
    return response


@router.delete("/kanban/cards/{card_id}", status_code=204)
async def delete_kanban_card(card_id: str):
    """Delete an existing kanban card."""
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    original_count = len(board["cards"])
    deleted_card = next((card for card in board["cards"] if card["id"] == card_id), None)
    board["cards"] = [card for card in board["cards"] if card["id"] != card_id]
    if len(board["cards"]) == original_count:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    board = _write_kanban_board(state_reader, board)
    if deleted_card is not None:
        timestamp = utcnow().isoformat()
        _apply_event_widget_triggers(
            state_reader,
            [
                {
                    "event_type": "kanban.card.deleted",
                    "timestamp": timestamp,
                    "payload": {
                        "event_type": "kanban.card.deleted",
                        "card": deleted_card,
                    },
                }
            ],
        )
        await _dispatch_workflow_triggers(
            [
                {
                    "event_type": "kanban.card.deleted",
                    "timestamp": timestamp,
                    "payload": {
                        "event_type": "kanban.card.deleted",
                        "card": deleted_card,
                        "timestamp": timestamp,
                        "source": "kanban",
                    },
                }
            ]
        )
        async with _kanban_card_trigger_lock:
            _remember_kanban_card_state(card_id, None)
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board))
    return None


@router.get("/tasks/{card_id}/content", response_model=TaskContentResponse)
async def get_task_content(card_id: str):
    """Read the markdown body for a single task card."""
    state_reader = get_state_reader()
    card = _find_kanban_card(state_reader, card_id)
    response = _read_task_content(state_reader, card)
    broadcast_task_content(card_id, response)
    return response


@router.put("/tasks/{card_id}/content", response_model=TaskContentResponse)
async def update_task_content(card_id: str, body: TaskContentUpdateRequest):
    """Persist the markdown body for a single task card."""
    state_reader = get_state_reader()
    _find_kanban_card(state_reader, card_id)
    result = _write_task_content(state_reader, card_id, body.content)
    _append_task_activity_entry(
        state_reader,
        card_id=card_id,
        kind="content",
        actor="user",
        message="Updated the task brief.",
        metadata={"path": result.path},
        created_at=utcnow().isoformat(),
    )
    broadcast_task_content(card_id, result)
    broadcast_task_activity(card_id)
    return result


@router.get("/tasks/{card_id}/activity", response_model=TaskActivityResponse)
async def get_task_activity(card_id: str):
    """Read the persisted activity timeline for a task card."""
    state_reader = get_state_reader()
    _find_kanban_card(state_reader, card_id)
    response = _task_activity_response(state_reader, card_id)
    broadcast_task_activity(card_id, response)
    return response


@router.post("/tasks/{card_id}/activity", response_model=TaskActivityEntryResponse)
async def create_task_activity(card_id: str, body: TaskActivityCreateRequest):
    """Append a user-visible activity entry for a task card."""
    state_reader = get_state_reader()
    _find_kanban_card(state_reader, card_id)
    created = _append_task_activity_entry(
        state_reader,
        card_id=card_id,
        kind=body.kind,
        actor=body.actor,
        message=body.message,
        metadata=body.metadata,
        created_at=utcnow().isoformat(),
    )
    broadcast_task_activity(card_id)
    return created


@router.post("/actions/run", response_model=ActionResponse)
async def action_run(body: ActionRunRequest):
    """Run a prompt/code action through the existing IPC message flow."""
    from . import main as chat_main

    if not chat_main.client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    code = body.code or body.content
    segments = []
    if body.widget_id:
        segments.append(f"Widget ID: {body.widget_id}")
    if body.language and code:
        segments.append(f"Run this {body.language} code:\n{code}")
    elif code:
        segments.append(f"Run this code:\n{code}")
    if body.prompt:
        segments.append(f"Prompt:\n{body.prompt}")
    if body.input:
        segments.append(f"Input:\n{body.input}")

    if not segments:
        raise HTTPException(status_code=400, detail="No run payload provided")

    thread_id = _resolve_action_thread_id(body.thread_id)
    metadata = _build_action_metadata(
        thread_id=thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source,
        widget_id=body.widget_id,
        root_widget_id=body.root_widget_id,
        action=body.invocation_source or body.language or "workspace.run",
    )
    upsert_thread(
        thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source or "workspace.run",
        root_widget_id=body.root_widget_id or body.widget_id,
        widget_id=body.widget_id,
    )

    try:
        response = await chat_main.client.send_message("\n\n".join(segments), metadata=metadata)
    except Exception as exc:
        fail_thread(thread_id, str(exc), widget_id=body.widget_id)
        raise

    complete_thread(thread_id, response.response, widget_id=body.widget_id)
    if body.broadcast_response:
        broadcast_chat_event(
            "agent_message",
            {
                "content": response.response,
                "metadata": {"widget_id": body.widget_id, "action": "run", "thread_id": thread_id},
                "timestamp": time.time(),
            },
        )
    _broadcast_widget_refresh(get_state_reader())
    return ActionResponse(
        ok=True,
        widget_id=body.widget_id,
        thread_id=thread_id,
        response=response.response,
    )


@router.post("/actions/generate", response_model=ActionResponse)
async def action_generate(body: ActionGenerateRequest):
    """Generate output through the existing IPC message flow."""
    from . import main as chat_main

    if not chat_main.client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    prompt = body.prompt
    if body.input:
        prompt = f"Input:\n{body.input}\n\n{prompt}"
    if body.target:
        prompt = f"Target: {body.target}\n\n{prompt}"
    if body.widget_id:
        prompt = f"Widget ID: {body.widget_id}\n\n{prompt}"

    thread_id = _resolve_action_thread_id(body.thread_id)
    metadata = _build_action_metadata(
        thread_id=thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source,
        widget_id=body.widget_id,
        root_widget_id=body.root_widget_id,
        action=body.invocation_source or "workspace.generate",
    )
    upsert_thread(
        thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source or "workspace.generate",
        root_widget_id=body.root_widget_id or body.widget_id,
        widget_id=body.widget_id,
    )

    try:
        response = await chat_main.client.send_message(prompt, metadata=metadata)
    except Exception as exc:
        fail_thread(thread_id, str(exc), widget_id=body.widget_id)
        raise

    complete_thread(thread_id, response.response, widget_id=body.widget_id)
    broadcast_chat_event(
        "agent_message",
        {
            "content": response.response,
            "metadata": {"widget_id": body.widget_id, "action": "generate", "thread_id": thread_id},
            "timestamp": time.time(),
        },
    )
    _broadcast_widget_refresh(get_state_reader())
    return ActionResponse(
        ok=True,
        widget_id=body.widget_id,
        thread_id=thread_id,
        response=response.response,
    )


@router.get("/threads", response_model=ThreadsResponse)
async def get_threads(
    invocation_type: Literal["user", "automation", "system"] | None = Query(default=None),
):
    """List tracked conversation/execution threads, optionally filtered by invocation type."""

    items = list_threads(invocation_type=invocation_type)
    return ThreadsResponse(items=items, total=len(items))


class TelegramStatusResponse(BaseModel):
    """Telegram integration status."""

    enabled: bool
    connected: bool
    chat_id: str = ""  # Masked for security
    web_clients_active: int
    will_forward_to_telegram: bool
    rate_limit_per_minute: int


@router.get("/telegram/status", response_model=TelegramStatusResponse)
async def get_telegram_status():
    """
    Get Telegram integration status.

    Shows whether Telegram is enabled, connected, and whether
    messages will be forwarded to Telegram (no web clients connected).
    """
    from . import main as chat_main
    from .telegram_client import get_telegram_client

    telegram = get_telegram_client()
    subscriber_counts = get_subscriber_counts()
    config = chat_main.config

    # Mask the chat_id for security
    chat_id = config.telegram_chat_id
    masked_chat_id = f"{chat_id[:4]}...{chat_id[-4:]}" if len(chat_id) > 8 else "*" * len(chat_id)

    return TelegramStatusResponse(
        enabled=config.telegram_enabled,
        connected=telegram is not None and telegram._started,
        chat_id=masked_chat_id if config.telegram_enabled else "",
        web_clients_active=subscriber_counts["stream"],
        will_forward_to_telegram=(
            telegram is not None and telegram._started and subscriber_counts["stream"] == 0
        ),
        rate_limit_per_minute=config.telegram_rate_limit_per_minute,
    )


class TelegramTestRequest(BaseModel):
    """Request to test Telegram integration."""

    message: str = "Test message from Progenitor"


class TelegramTestResponse(BaseModel):
    """Response from Telegram test."""

    success: bool
    message: str


@router.post("/telegram/test", response_model=TelegramTestResponse)
async def test_telegram():
    """
    Send a test message to Telegram.

    Used to verify the Telegram integration is working correctly.
    """
    from .telegram_client import get_telegram_client

    telegram = get_telegram_client()
    if not telegram:
        return TelegramTestResponse(
            success=False, message="Telegram integration not enabled or not configured"
        )

    try:
        success = await telegram.send_message(
            "🧪 Test message from Progenitor Backend\n\n"
            "If you see this, Telegram integration is working!"
        )
        if success:
            return TelegramTestResponse(success=True, message="Test message sent successfully")
        else:
            return TelegramTestResponse(
                success=False, message="Failed to send test message - check logs for details"
            )
    except Exception as e:
        logger.error("Telegram test failed: %s", e)
        return TelegramTestResponse(success=False, message=f"Error: {str(e)}")


@router.get("/tasks", response_model=TasksResponse)
async def get_active_tasks():
    """
    Get the current kanban task list for lightweight consumers.
    """
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    tasks = [
        TaskInfo(
            id=card["id"],
            title=card["title"],
            status=card["status"],
            description=card.get("summary") or card.get("description") or None,
            progress=100 if card["status"] == "complete" else None,
            column_id=card["column_id"],
            owner=card.get("owner"),
            agent_name=card.get("agent_name"),
            labels=list(card.get("labels") or []),
            updated_at=card.get("updated_at"),
            task_path=_task_content_public_path(card["id"]),
        )
        for card in board["cards"]
        if not bool((card.get("metadata") or {}).get("archived_at"))
    ]
    tasks.sort(
        key=lambda task: (
            task.status == "complete",
            task.column_id or "",
            task.updated_at or "",
        ),
        reverse=False,
    )
    return TasksResponse(tasks=tasks, total=len(tasks))


@router.get("/alerts", response_model=AlertsResponse)
async def get_alerts():
    """
    Get backend-managed alert history.

    Returns alerts generated by the progenitor agent.
    """
    return AlertsResponse.model_validate(_alerts_payload())


@router.post("/alerts/viewed", response_model=MarkAlertsViewedResponse)
async def mark_viewed_alerts(body: MarkAlertsViewedRequest | None = None):
    """Mark alerts viewed without removing them from history."""
    updated = mark_alerts_viewed(body.alert_ids if body else None)
    broadcast_alerts_state()
    return MarkAlertsViewedResponse(updated=updated, unread=alert_summary()["unread"])


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str):
    """
    Dismiss an alert.

    Alerts remain in history; dismissing only clears unread state.
    """
    alert = get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    mark_alerts_viewed([alert_id])
    broadcast_alerts_state()
    return {"status": "dismissed", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/respond")
async def respond_to_alert(alert_id: str, response: str = "acknowledged"):
    """
    Respond to an alert.

    Acknowledges the alert and optionally sends a response back to the agent.
    """
    alert = get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    mark_alerts_viewed([alert_id])
    broadcast_alerts_state()
    logger.info("Alert %s responded with: %s", alert_id, response)

    return {"status": "responded", "alert_id": alert_id, "response": response}


class AgentDetailResponse(BaseModel):
    """Detailed information about a single agent."""

    name: str
    status: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    transport: str = "stdio"
    enabled: bool = True
    # Statistics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    avg_response_time: float = 0.0
    last_active: str | None = None
    uptime_seconds: float = 0.0
    # Recent activity
    recent_requests: list[dict] = []
    # Performance
    success_rate: float = 100.0
    tokens_per_request_avg: float = 0.0


@router.get("/agents/{agent_name}", response_model=AgentDetailResponse)
async def get_agent_detail(agent_name: str):
    """
    Get detailed information about a specific agent.

    Returns agent configuration, statistics, and recent activity.
    """
    state_reader = get_state_reader()
    agents, _ = state_reader.get_registry()
    health = state_reader.get_health()
    progenitor_running = health.is_running()

    # Find the agent
    agent = next((a for a in agents if a.name == agent_name), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return AgentDetailResponse(
        name=agent.name,
        status="ready" if progenitor_running else "offline",
        kind=agent.kind,
        version=agent.version,
        description=agent.description,
        transport=agent.transport,
        enabled=agent.enabled,
        uptime_seconds=getattr(health, "uptime_seconds", 0),
    )


@router.get("/prompts", response_model=PromptsResponse)
async def get_prompts(agent: str | None = None):
    """
    Get available prompt templates from the mounted prompts directory.

    Returns prompt files recursively, including their content and inferred agent
    associations for the workspace prompt library.
    """
    service = PromptService()
    prompts = [
        PromptFileResponse.model_validate(prompt, from_attributes=True)
        for prompt in service.list_prompts(agent=agent)
    ]
    if agent is None:
        # Seed every /api/ui/stream subscriber with the full list (the prompts
        # store slice renders from `prompts.snapshot`). Skip filtered queries so
        # the broadcast always carries the complete catalogue.
        broadcast_prompts_snapshot(prompts)
    return PromptsResponse(prompts=prompts, total=len(prompts))


@router.get("/prompts/content", response_model=PromptFileResponse)
async def get_prompt_content(path: str = Query(..., min_length=1)):
    """Get a single prompt file from the mounted prompts directory."""
    try:
        return PromptFileResponse.model_validate(
            PromptService().get_prompt(path), from_attributes=True
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/prompts/content", response_model=PromptFileResponse)
async def update_prompt_content(request: PromptUpdateRequest):
    """Persist edits to an existing prompt file."""
    try:
        saved = PromptFileResponse.model_validate(
            PromptService().save_prompt(request.path, request.content),
            from_attributes=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{request.path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_prompts_snapshot()
    return saved


@router.get("/skills", response_model=SkillsResponse)
async def get_skills(agent: str | None = None):
    """Get available DeepAgent skills from the mounted skills directory."""
    try:
        skills = [
            SkillFileResponse.model_validate(skill, from_attributes=True)
            for skill in SkillService().list_skills(agent=agent)
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent is None:
        # Seed every /api/ui/stream subscriber with the full list (the skills
        # store slice renders from `skills.snapshot`). Skip filtered queries so
        # the broadcast always carries the complete catalogue.
        broadcast_skills_snapshot(skills)
    return SkillsResponse(skills=skills, total=len(skills))


@router.get("/skills/content", response_model=SkillFileResponse)
async def get_skill_content(path: str = Query(..., min_length=1)):
    """Get a single skill file from the mounted skills directory."""
    try:
        return SkillFileResponse.model_validate(
            SkillService().get_skill(path), from_attributes=True
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Skill '{path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/skills/content", response_model=SkillFileResponse)
async def update_skill_content(request: SkillUpdateRequest):
    """Persist edits to an existing skill file."""
    try:
        saved = SkillFileResponse.model_validate(
            SkillService().save_skill(request.path, request.content),
            from_attributes=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Skill '{request.path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_skills_snapshot()
    return saved


@router.post("/skills", response_model=SkillFileResponse, status_code=201)
async def create_skill(request: SkillCreateRequest):
    """Create a new DeepAgent skill file."""
    try:
        created = SkillFileResponse.model_validate(
            SkillService().create_skill(request.agent, request.name, request.content),
            from_attributes=True,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"Skill '{exc}' already exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_skills_snapshot()
    return created


@router.get("/logs")
async def get_logs(
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    file_path: str | None = Query(default=None, alias="file"),
    start_date: str | None = Query(default=None, alias="start"),
    end_date: str | None = Query(default=None, alias="end"),
    after: str | None = None,
    limit: int = 500,
    format: str = "text",
    search_mode: str = "plain",
):
    """
    Get agent logs with optional filtering.

    Args:
        level: Filter by log level (debug, info, warn, error)
        search: Search string to filter logs
        limit: Maximum number of lines to return (default 500)
        format: Response format - "text" for raw logs, "json" for structured

    Returns recent logs from the agent.
    """
    state_reader = get_state_reader()
    if search_mode not in {"plain", "regex"}:
        raise HTTPException(status_code=400, detail="search_mode must be 'plain' or 'regex'")

    source_groups = state_reader.get_log_sources()
    default_end = datetime.now(UTC).date()
    default_start = default_end - timedelta(days=6)
    start_date = start_date or default_start.isoformat()
    end_date = end_date or default_end.isoformat()
    if not any(value is not None for value in (level, search, source, folder, file_path)):
        candidate_path = None
        for log_path in state_reader._iter_log_files():
            candidate_result = state_reader.query_logs(
                folder=state_reader._log_group(log_path),
                file_path=state_reader._relative_log_path(log_path),
                start_date=start_date,
                end_date=end_date,
                limit=1,
            )
            if candidate_result["entries"]:
                candidate_path = log_path
                break

        if candidate_path is None:
            candidate_path = next(iter(state_reader._iter_log_files()), None)

        if candidate_path is not None:
            folder = state_reader._log_group(candidate_path)
            file_path = state_reader._relative_log_path(candidate_path)
        else:
            for group in source_groups:
                options = group.get("options", [])
                if options:
                    folder = str(group.get("id") or "")
                    file_path = str(options[0].get("id") or "")
                    break

    try:
        result = state_reader.query_logs(
            level=level,
            search=search,
            source=source,
            folder=folder,
            file_path=file_path,
            start_date=start_date,
            end_date=end_date,
            after=after,
            limit=limit,
            search_mode=search_mode,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

    entries = result["entries"]
    folders = state_reader.get_log_folders()

    if format == "json":
        return {
            "entries": entries,
            "total": result["total"],
            "returned": result["returned"],
            "filtered": any(
                value is not None
                for value in (level, search, source, folder, file_path, start_date, end_date)
            ),
            "source_groups": source_groups,
            "folders": folders,
            "newest_cursor": result["newest_cursor"],
            "cursor_found": result["cursor_found"],
            "date_range": {"start": start_date, "end": end_date},
            "active_selection": {
                "group": folder,
                "file": file_path,
            },
        }

    content = "\n".join(
        f"[{entry['time']}] [{entry['level'].upper()}] [{entry['source']}] {entry['message']}"
        for entry in entries
    )
    return {"logs": content, "total": len(entries)}


@router.get("/logs/sources")
async def get_log_sources():
    """Return available log groups/files without loading log entries."""
    state_reader = get_state_reader()
    return {
        "source_groups": state_reader.get_log_sources(),
        "folders": state_reader.get_log_folders(),
    }


@router.get("/logs/export")
async def export_logs(
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    file_path: str | None = Query(default=None, alias="file"),
    start_date: str | None = Query(default=None, alias="start"),
    end_date: str | None = Query(default=None, alias="end"),
    search_mode: str = "plain",
    format: str = "text",
):
    """Export logs using the current filters."""
    state_reader = get_state_reader()
    if search_mode not in {"plain", "regex"}:
        raise HTTPException(status_code=400, detail="search_mode must be 'plain' or 'regex'")

    default_end = datetime.now(UTC).date()
    default_start = default_end - timedelta(days=6)
    start_date = start_date or default_start.isoformat()
    end_date = end_date or default_end.isoformat()

    try:
        result = state_reader.query_logs(
            level=level,
            search=search,
            source=source,
            folder=folder,
            file_path=file_path,
            start_date=start_date,
            end_date=end_date,
            limit=0,
            search_mode=search_mode,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc

    entries = result["entries"]
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    base_name = f"progenitor_logs_{timestamp}"

    if format == "json":
        return Response(
            content=json.dumps(entries, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'},
        )

    if format == "jsonl":
        content = "\n".join(json.dumps(entry) for entry in entries)
        return Response(
            content=content,
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.jsonl"'},
        )

    if format == "csv":
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["timestamp", "level", "source", "relative_path", "message"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "timestamp": entry.get("time") or entry.get("timestamp") or "",
                    "level": entry.get("level") or "",
                    "source": entry.get("source") or "",
                    "relative_path": entry.get("relative_path") or "",
                    "message": entry.get("message") or "",
                }
            )
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'},
        )

    content = "\n".join(
        f"[{entry.get('time') or '--'}] [{str(entry.get('level', 'info')).upper()}] "
        f"[{entry.get('relative_path') or entry.get('source') or 'system'}] {entry.get('message', '')}"
        for entry in entries
    )
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.log"'},
    )


def create_alert(
    type: str, title: str, message: str, urgent: bool = False, requires_response: bool = False
) -> AlertInfo:
    """Create and store a backend-managed alert record."""
    return store_alert(
        type=type,
        title=title,
        message=message,
        urgent=urgent,
        requires_response=requires_response,
    )


# SSE heartbeat interval in seconds.  The SSE connection itself is the primary
# liveness signal; heartbeats exist only to prevent TCP-level timeouts on idle
# streams.  Override with SSE_HEARTBEAT_INTERVAL env var.
def _parse_heartbeat_interval(raw: str, default: float = 15.0) -> float:
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError("must be positive")
        return val
    except ValueError:
        logger.warning(
            "Invalid SSE_HEARTBEAT_INTERVAL %r — must be a positive number; using %.0fs",
            raw,
            default,
        )
        return default


_SSE_HEARTBEAT_INTERVAL: float = _parse_heartbeat_interval(
    os.getenv("SSE_HEARTBEAT_INTERVAL", "15")
)

# Event subscribers for SSE - split into chat and status streams
_chat_subscribers: set[asyncio.Queue] = set()  # For IPC events (chat, tasks, alerts)
_status_subscribers: set[asyncio.Queue] = set()  # For state file status updates
_ui_stream_subscribers: set[asyncio.Queue] = set()  # Canonical typed UI event envelopes
_ui_event_seq = 0
_ui_event_replay: list[UiEventEnvelope] = []
_UI_EVENT_REPLAY_LIMIT = 200

# Shutdown event for SSE connections
_sse_shutdown_event: asyncio.Event | None = None

# Status monitor state
_status_monitor_task: asyncio.Task | None = None
_last_status: dict = {}
_state_watch_callback = None
_state_watch_push_task: asyncio.Task | None = None

# Track active SSE response tasks so we can cancel them on shutdown
_active_chat_tasks: set[asyncio.Task] = set()
_active_status_tasks: set[asyncio.Task] = set()
# Keep a strong reference to fire-and-forget agent-profile broadcasts so the
# event loop's weak task reference can't let one get garbage-collected mid-run.
_active_agent_profile_broadcast_tasks: set[asyncio.Task] = set()
_active_ui_stream_tasks: set[asyncio.Task] = set()


def has_active_ui_stream_subscribers() -> bool:
    """Check if there are any active canonical UI stream subscribers.

    Telegram fallback should follow the modern ``/api/ui/stream`` connection,
    not the legacy ``/api/ui/events`` subscriber list.
    """
    return len(_ui_stream_subscribers) > 0


def get_subscriber_counts() -> dict[str, int]:
    """Get counts of all subscriber types.

    Returns:
        Dictionary with chat and status subscriber counts.
    """
    return {
        "chat": len(_chat_subscribers),
        "status": len(_status_subscribers),
        "stream": len(_ui_stream_subscribers),
        "web": len(_ui_stream_subscribers),
        "total": len(_chat_subscribers) + len(_status_subscribers) + len(_ui_stream_subscribers),
    }


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""
    from . import main as chat_main

    state_reader = get_state_reader()
    snapshot = state_reader.get_snapshot()

    now = time.time()
    start_time = getattr(chat_main, "_server_start_time", now)
    shutdown_mgr = getattr(chat_main, "shutdown_manager", None)

    status = {
        "status": "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": snapshot.health.is_running(),
        "progenitor_status": snapshot.health.status,  # Raw status from the health document
        "version": getattr(chat_main.config, "api_version", "0.1.0"),
        # State data
        "cycle_count": snapshot.health.cycle_count,  # Use health.cycle_count for accuracy
        "thread_id": snapshot.health.thread_id or snapshot.thread_id,
        "messages_count": snapshot.messages_count,
        "pid": snapshot.health.pid,
        "last_active": snapshot.health.last_active,
        # Distinct from "version" above (the backend's own api_version) - this
        # is the runtime process's code commit, for spotting a stale-process
        # running old code without cross-referencing Postgres by hand.
        "progenitor_commit": snapshot.health.version,
        "progenitor_started_at": snapshot.health.started_at,
        "llm_model": snapshot.health.llm_model,
        "llm_reasoning_enabled": snapshot.health.llm_reasoning_enabled,
        "llm_reasoning_effort": snapshot.health.llm_reasoning_effort,
        # Loops
        "loops": [
            {
                "name": loop_item.name,
                "status": loop_item.status,
                "current_task": loop_item.current_task,
            }
            for loop_item in snapshot.loops
        ],
        # Agents
        "agents": [
            {
                "name": a.name,
                "kind": a.kind,
                "version": a.version,
                "description": a.description,
                "enabled": a.enabled,
                "status": "ready" if snapshot.health.is_running() else "offline",
            }
            for a in snapshot.agents
        ],
        "tools": [
            {"name": tool.name, "description": tool.description, "enabled": tool.enabled}
            for tool in snapshot.tools
        ],
        # Backlog
        "backlog": [
            {"id": b.id, "type": b.item_type, "status": b.status, "description": b.description}
            for b in snapshot.backlog
        ],
        # Schedule
        "schedule": [
            {"id": s.id, "name": s.name, "schedule": s.schedule, "enabled": s.enabled}
            for s in snapshot.schedule
        ],
        # Build status
        "build": {
            "in_progress": snapshot.build.in_progress if snapshot.build else False,
            "phase": snapshot.build.phase if snapshot.build else None,
            "task_description": snapshot.build.task_description if snapshot.build else None,
            "started_at": snapshot.build.started_at if snapshot.build else None,
            "iteration": snapshot.build.iteration if snapshot.build else 0,
            "score": snapshot.build.score if snapshot.build else 0.0,
            "message_count": snapshot.build.message_count if snapshot.build else 0,
        }
        if snapshot.build
        else None,
        "widgets": state_reader.get_widgets(),
        "sessions": {
            "total": snapshot.sessions.total if snapshot.sessions else 0,
            "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
            "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
        },
        "alerts": _alerts_payload(),
    }

    if shutdown_mgr and shutdown_mgr.is_shutting_down:
        status["status"] = "draining"

    # Memory info
    try:
        import psutil

        process = psutil.Process()
        status["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except ImportError:
        status["memory_mb"] = None
    except Exception:
        status["memory_mb"] = None

    return status


def _process_metrics() -> dict:
    """Process-level status that changes without a DB write (uptime, memory).

    The full, DB-derived status is pushed on connect (connection.ready) and on
    every change via the Postgres NOTIFY watcher; this lightweight payload is
    merged into it by the periodic tick so we never re-read the DB just to
    refresh metrics.

    Also includes ``progenitor_running``: a live IPC-heartbeat liveness check,
    deliberately NOT stored in Postgres (see HealthInfo.live_reachable), so a
    pure liveness flip - the runtime dying with no other document changing -
    still reaches the frontend within this tick's cadence instead of only
    surfacing on the next incidental Postgres write.
    """
    from . import main as chat_main

    now = time.time()
    start_time = getattr(chat_main, "_server_start_time", now)
    shutdown_mgr = getattr(chat_main, "shutdown_manager", None)
    draining = bool(shutdown_mgr and shutdown_mgr.is_shutting_down)

    metrics: dict = {
        "status": "draining" if draining else "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": bool(chat_main.get_progenitor_liveness()),
    }
    try:
        import psutil

        metrics["memory_mb"] = round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
    except Exception:
        metrics["memory_mb"] = None
    return metrics


async def _status_monitor():
    """Push process-level metrics to SSE subscribers on a fixed cadence.

    Only metrics (uptime/memory) are sent here; DB-derived state is carried by
    the LISTEN/NOTIFY watcher, so this loop never reads the database.
    """
    update_interval = 3  # Refresh process metrics every 3 seconds

    logger.info("[StatusMonitor] Starting process-metrics loop")

    while True:
        try:
            await asyncio.sleep(update_interval)

            if not _status_subscribers and not _ui_stream_subscribers:
                continue  # No subscribers, skip

            broadcast_status_event("status", _process_metrics())

        except asyncio.CancelledError:
            print("[StatusMonitor] Cancelled", flush=True)
            break
        except Exception as e:
            print(f"[StatusMonitor] Error: {e}", flush=True)
            await asyncio.sleep(10)


async def _broadcast_latest_status() -> None:
    global _last_status, _state_watch_push_task
    try:
        current = await _get_current_status()
        _last_status = current.copy()
        broadcast_status_event("status", current)
        if _ui_stream_subscribers:
            _broadcast_chat_sessions_snapshot()
    finally:
        _state_watch_push_task = None


# Store keys whose change should refresh the unified widget list.
_WIDGET_KEYS = frozenset({"mcp", "mcp_tools", "schedule", "registry", "event_widgets"})


async def _broadcast_agent_profiles_safe() -> None:
    try:
        await broadcast_agent_profiles_snapshot(get_agent_store, broadcast_ui_event)
    except Exception:
        logger.debug("Failed to broadcast agent profiles snapshot", exc_info=True)


def _queue_state_watch_status(snapshot, changed_key: str | None = None) -> None:
    global _state_watch_push_task
    if not _status_subscribers and not _ui_stream_subscribers:
        return
    # Route the changed key to its dedicated event so pages render from a precise
    # snapshot (None = full resync after a reconnect → refresh everything).
    if _ui_stream_subscribers:
        try:
            if (
                changed_key is None
                or changed_key == _kanban_board_store_key()
                or changed_key.startswith(_KANBAN_CARD_KEY_PREFIX)
            ):
                broadcast_kanban_snapshot()
            if changed_key is None or changed_key.startswith(INBOX_MESSAGE_KEY_PREFIX):
                _broadcast_inbox_snapshot()
            if changed_key in (None, "mcp", "mcp_tools"):
                broadcast_mcp_snapshot()
            if changed_key in (None, "schedule"):
                broadcast_schedule_snapshot()
            if changed_key in (None, "knowledge"):
                broadcast_knowledge_snapshot()
            if changed_key in (None, "registry"):
                # Async (reads the agent store off-thread), so it's a fire-and-forget
                # task rather than an inline call like the sync broadcasts above.
                # A strong reference is kept in _active_agent_profile_broadcast_tasks
                # (and dropped on completion) since asyncio only holds a weak
                # reference to unreferenced tasks, which risks premature GC.
                profile_task = asyncio.create_task(_broadcast_agent_profiles_safe())
                _active_agent_profile_broadcast_tasks.add(profile_task)
                profile_task.add_done_callback(_active_agent_profile_broadcast_tasks.discard)
            if changed_key is None:
                board = _read_kanban_board(get_state_reader())
                for card in board["cards"]:
                    card_id = str(card.get("id") or "").strip()
                    if not card_id:
                        continue
                    broadcast_task_content(card_id)
                    broadcast_task_activity(card_id)
            elif changed_key.startswith("kanban_task_content:"):
                broadcast_task_content(changed_key.split(":", 1)[1])
            elif changed_key.startswith("kanban_task_activity:"):
                broadcast_task_activity(changed_key.split(":", 1)[1])
            if changed_key is None or changed_key.startswith(WORKFLOW_DEFINITION_KEY_PREFIX):
                broadcast_workflows_snapshot()
                if changed_key is not None:
                    workflow_id = changed_key.removeprefix(WORKFLOW_DEFINITION_KEY_PREFIX)
                    if workflow_id:
                        broadcast_workflow_detail(workflow_id)
            if (
                changed_key is None
                or changed_key.startswith(WORKFLOW_RUN_KEY_PREFIX)
                or changed_key.startswith(WORKFLOW_QUEUE_KEY_PREFIX)
            ):
                broadcast_workflow_runs_snapshot()
            if changed_key is None or changed_key in _WIDGET_KEYS:
                broadcast_widgets_snapshot()
        except Exception:
            logger.debug("Failed to broadcast dedicated snapshot", exc_info=True)
    # Status (health/agents/loops/sessions) + chat sessions follow on the
    # coalesced task.
    if _state_watch_push_task and not _state_watch_push_task.done():
        return
    _state_watch_push_task = asyncio.create_task(_broadcast_latest_status())


_kanban_trigger_watch_callback = None
_active_kanban_reconcile_tasks: set[asyncio.Task] = set()


def _queue_kanban_trigger_reconciliation(_snapshot, changed_key: str | None = None) -> None:
    """Always-on watcher that reconciles kanban.card.* workflow triggers for
    changes made outside the REST kanban routes -- chiefly the runtime agent
    writing /state/tasks/<id>.md directly.

    Registered separately from _queue_state_watch_status, which returns
    immediately when no UI client is connected (fine for UI-push
    optimization, wrong here: workflows must keep firing whether or not
    anyone has the frontend open).
    """
    task = asyncio.create_task(_reconcile_kanban_card_triggers(changed_key))
    _active_kanban_reconcile_tasks.add(task)
    task.add_done_callback(_active_kanban_reconcile_tasks.discard)


async def start_state_status_watcher(reconnect_delay: float = 2.0) -> None:
    global _state_watch_callback, _kanban_trigger_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is None:
        _state_watch_callback = _queue_state_watch_status
        state_reader.add_watcher(_state_watch_callback)
    if _kanban_trigger_watch_callback is None:
        _kanban_trigger_watch_callback = _queue_kanban_trigger_reconciliation
        state_reader.add_watcher(_kanban_trigger_watch_callback)
    await state_reader.start_watching(reconnect_delay)


async def stop_state_status_watcher() -> None:
    global _state_watch_callback, _kanban_trigger_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is not None:
        state_reader.remove_watcher(_state_watch_callback)
        _state_watch_callback = None
    if _kanban_trigger_watch_callback is not None:
        state_reader.remove_watcher(_kanban_trigger_watch_callback)
        _kanban_trigger_watch_callback = None
    await state_reader.stop_watching()


def _status_changed(current: dict, previous: dict) -> bool:
    """Check if status has meaningfully changed."""
    if not previous:
        return True

    # Check key fields for changes
    for key in ("status", "progenitor_running"):
        if current.get(key) != previous.get(key):
            return True

    # Check memory change > 10MB
    curr_mem: float = current.get("memory_mb") or 0
    prev_mem: float = previous.get("memory_mb") or 0
    return abs(curr_mem - prev_mem) > 10


def start_status_monitor():
    """Start the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task is None or _status_monitor_task.done():
        _status_monitor_task = asyncio.create_task(_status_monitor())
        print("[StatusMonitor] Started background task", flush=True)


def stop_status_monitor():
    """Stop the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task and not _status_monitor_task.done():
        _status_monitor_task.cancel()
        logger.info("Status monitor stopped")


def get_sse_shutdown_event() -> asyncio.Event:
    """Get or create the SSE shutdown event."""
    global _sse_shutdown_event
    if _sse_shutdown_event is None:
        _sse_shutdown_event = asyncio.Event()
    return _sse_shutdown_event


async def close_all_sse_connections():
    """
    Signal all SSE connections to close gracefully.

    Called during server shutdown to ensure SSE connections do not block
    the graceful shutdown process.
    """
    global _sse_shutdown_event, _active_chat_tasks, _active_status_tasks, _active_ui_stream_tasks

    # Count all subscriber types
    chat_count = len(_chat_subscribers)
    status_count = len(_status_subscribers)
    stream_count = len(_ui_stream_subscribers)
    total_count = chat_count + status_count + stream_count

    logger.info(
        "Closing SSE connections: %d chat, %d status, %d stream (total: %d)",
        chat_count,
        status_count,
        stream_count,
        total_count,
    )

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = get_sse_shutdown_event()
    shutdown_event.set()

    # Send shutdown event to all subscriber types
    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}',
    }

    for queue in _chat_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    for queue in _status_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    for queue in _ui_stream_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    all_tasks = [*_active_chat_tasks, *_active_status_tasks, *_active_ui_stream_tasks]
    tasks_to_cancel = [t for t in all_tasks if not t.done()]
    if tasks_to_cancel:
        logger.info("Force-cancelling %d SSE tasks", len(tasks_to_cancel))
        for task in tasks_to_cancel:
            task.cancel()
        # Wait for cancellation with timeout
        done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0)
        if pending:
            logger.warning("%d SSE tasks did not cancel in time", len(pending))

    # Clear all lists
    _active_chat_tasks.clear()
    _active_status_tasks.clear()
    _active_ui_stream_tasks.clear()

    logger.info("SSE shutdown signal sent to all connections")


# ============================================================================
# NEW SPLIT SSE ENDPOINTS
# ============================================================================


def _next_ui_envelope(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    scope: Literal["global", "session", "chat"] = "global",
    session_id: str | None = None,
) -> UiEventEnvelope:
    global _ui_event_seq
    _ui_event_seq += 1
    return UiEventEnvelope(
        id=f"ui-{_ui_event_seq}",
        seq=_ui_event_seq,
        type=event_type,
        scope=scope,
        session_id=session_id,
        payload=dict(payload),
    )


async def _generate_ui_stream(
    last_event_id: str | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Generate the canonical typed UI event stream."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _ui_stream_subscribers.add(queue)

    current_task = asyncio.current_task()
    if current_task:
        _active_ui_stream_tasks.add(current_task)

    shutdown_event = get_sse_shutdown_event()
    start_status_monitor()

    try:
        if last_event_id:
            try:
                replay_after = int(str(last_event_id).removeprefix("ui-") or 0)
            except ValueError:
                replay_after = 0
            for envelope in list(_ui_event_replay):
                if envelope.seq > replay_after:
                    yield ServerSentEvent(
                        event="ui.event",
                        data=envelope.model_dump(),
                        id=envelope.id,
                    )

        initial_status = await _get_current_status()
        connected = _next_ui_envelope(
            "connection.ready",
            {"stream": "ui", "status": initial_status, "alerts": _alerts_payload()},
        )
        yield ServerSentEvent(event="ui.event", data=connected.model_dump(), id=connected.id)

        while True:
            if shutdown_event.is_set():
                envelope = _next_ui_envelope(
                    "connection.shutdown",
                    {"reason": "server_shutdown", "message": "Server is shutting down"},
                )
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_INTERVAL)
                if event.get("type") == "shutdown":
                    envelope = _next_ui_envelope(
                        "connection.shutdown",
                        {"reason": "server_shutdown", "message": "Server is shutting down"},
                    )
                    yield ServerSentEvent(
                        event="ui.event", data=envelope.model_dump(), id=envelope.id
                    )
                    break
                envelope = event["envelope"]
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
            except TimeoutError:
                envelope = _next_ui_envelope("connection.heartbeat", {"timestamp": time.time()})
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
    finally:
        _ui_stream_subscribers.discard(queue)
        if current_task:
            _active_ui_stream_tasks.discard(current_task)


async def _generate_chat_events() -> AsyncGenerator[ServerSentEvent, None]:
    """Generate SSE events for chat/IPC stream only.

    This stream receives:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages
    - agent_question: Agent questions for the user
    - alert: System alerts
    - task: Task status updates
    - agent: Agent activity updates

    Does NOT receive status/health updates (use /status for those).
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _chat_subscribers.add(queue)

    current_task = asyncio.current_task()
    if current_task:
        _active_chat_tasks.add(current_task)

    shutdown_event = get_sse_shutdown_event()

    try:
        yield ServerSentEvent(
            event="connected",
            data={"stream": "chat", "timestamp": time.time()},
        )

        while True:
            if shutdown_event.is_set():
                logger.debug("Chat SSE connection closing due to server shutdown")
                yield ServerSentEvent(
                    event="shutdown",
                    data={"reason": "server_shutdown", "message": "Server is shutting down"},
                )
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_INTERVAL)
                if event.get("type") == "shutdown":
                    yield ServerSentEvent(event="shutdown", raw_data=event["data"])
                    break
                yield ServerSentEvent(event=event["type"], raw_data=event["data"])
            except TimeoutError:
                if shutdown_event.is_set():
                    yield ServerSentEvent(
                        event="shutdown",
                        data={"reason": "server_shutdown", "message": "Server is shutting down"},
                    )
                    break
                yield ServerSentEvent(event="heartbeat", data={"timestamp": time.time()})
    finally:
        _chat_subscribers.discard(queue)
        if current_task:
            _active_chat_tasks.discard(current_task)


async def _generate_status_events() -> AsyncGenerator[ServerSentEvent, None]:
    """Generate SSE events for status/health stream only.

    This stream receives:
    - status: Full status updates from state files
    - heartbeat: Periodic heartbeats with status

    Does NOT receive chat/IPC events (use /events for those).
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _status_subscribers.add(queue)

    current_task = asyncio.current_task()
    if current_task:
        _active_status_tasks.add(current_task)

    shutdown_event = get_sse_shutdown_event()
    start_status_monitor()

    try:
        initial_status = await _get_current_status()
        yield ServerSentEvent(event="connected", data=initial_status)
        yield ServerSentEvent(event="alerts", data=_alerts_payload())

        while True:
            if shutdown_event.is_set():
                logger.debug("Status SSE connection closing due to server shutdown")
                yield ServerSentEvent(
                    event="shutdown",
                    data={"reason": "server_shutdown", "message": "Server is shutting down"},
                )
                break

            try:
                event = await asyncio.wait_for(queue.get(), timeout=_SSE_HEARTBEAT_INTERVAL)
                if event.get("type") == "shutdown":
                    yield ServerSentEvent(event="shutdown", raw_data=event["data"])
                    break
                yield ServerSentEvent(event=event["type"], raw_data=event["data"])
            except TimeoutError:
                if shutdown_event.is_set():
                    yield ServerSentEvent(
                        event="shutdown",
                        data={"reason": "server_shutdown", "message": "Server is shutting down"},
                    )
                    break
                yield ServerSentEvent(event="heartbeat", data=_last_status or {"status": "healthy"})
    finally:
        _status_subscribers.discard(queue)
        if current_task:
            _active_status_tasks.discard(current_task)


@router.get("/stream", response_class=EventSourceResponse)
async def ui_stream(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    response: Response = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Canonical typed SSE stream for all frontend UI updates."""
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_ui_stream(last_event_id):
        yield event


@router.get("/events", response_class=EventSourceResponse)
async def events_stream(response: Response = None) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Server-Sent Events endpoint for IPC events.

    Connect to receive real-time notifications about:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages (talk to user)
    - agent_question: Agent questions for the user
    - alert: System alerts requiring attention
    - task: Task status updates
    - agent: Agent activity updates

    This stream does NOT include status/health updates.
    Use /status for continuous health monitoring.

    Events are formatted as:
        event: <type>
        data: <json>

    Example event types:
    - scheduled_response: {"content": "...", "metadata": {...}, "timestamp": ...}
    - agent_message: {"content": "...", "metadata": {...}, "timestamp": ...}
    - agent_question: {"content": "...", "metadata": {...}, "timestamp": ...}
    - alert: {"id": "...", "type": "...", "title": "...", "message": "...", "urgent": false}
    """
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_chat_events():
        yield event


@router.get("/status", response_class=EventSourceResponse)
async def status_stream(response: Response = None) -> AsyncGenerator[ServerSentEvent, None]:
    """
    Server-Sent Events endpoint for continuous status updates.

    Connect to receive real-time status updates about:
    - Server health and uptime
    - Progenitor agent status
    - Cycle count and activity
    - Memory usage
    - Registered agents and tools
    - Backlog and schedule items
    - Build status

    This stream does NOT include chat/IPC events.
    Use /events for chat messages and alerts.

    Events are formatted as:
        event: <type>
        data: <json>

    Event types:
    - connected: Initial connection with full status
    - status: Periodic status updates (every ~3 seconds)
    - heartbeat: Keepalive with status (during quiet periods)
    - shutdown: Server shutting down
    """
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_status_events():
        yield event


# ============================================================================
# BROADCAST FUNCTIONS
# ============================================================================


def broadcast_ui_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    scope: Literal["global", "session", "chat"] = "global",
    session_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast one typed event envelope to canonical /api/ui/stream subscribers."""
    envelope = _next_ui_envelope(event_type, payload, scope=scope, session_id=session_id)
    _ui_event_replay.append(envelope)
    del _ui_event_replay[:-_UI_EVENT_REPLAY_LIMIT]

    event = {"type": "ui.event", "envelope": envelope}
    dead: list[asyncio.Queue] = []
    for queue in _ui_stream_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for UI stream subscriber — evicting dead connection")
            dead.append(queue)
    for q in dead:
        _ui_stream_subscribers.discard(q)
    return envelope


def broadcast_schedule_snapshot() -> UiEventEnvelope:
    """Broadcast the current schedule list to canonical /api/ui/stream subscribers.

    Emitted after backend-side schedule mutations and whenever the runtime
    reports a schedule change (fire / chat-driven CRUD) via the IPC event
    bridge, so the Schedules page reflects changes live.
    """
    state_reader = get_state_reader()
    items = [
        _schedule_item_to_info(item).model_dump(mode="json") for item in state_reader.get_schedule()
    ]
    return broadcast_ui_event("schedule.snapshot", {"items": items})


def broadcast_kanban_snapshot(board: KanbanBoardResponse | None = None) -> UiEventEnvelope:
    """Broadcast the current Tasks board to /api/ui/stream subscribers."""
    state_reader = get_state_reader()
    payload = (board or _kanban_board_response(state_reader)).model_dump(mode="json")
    return broadcast_ui_event("kanban.snapshot", payload)


def broadcast_task_content(
    card_id: str, content: TaskContentResponse | None = None
) -> UiEventEnvelope:
    """Broadcast the canonical task-body payload for one card."""
    state_reader = get_state_reader()
    if content is None:
        card = _find_kanban_card(state_reader, card_id)
        content = _read_task_content(state_reader, card)
    return broadcast_ui_event("task.content.updated", content.model_dump(mode="json"))


def broadcast_task_activity(
    card_id: str, activity: TaskActivityResponse | None = None
) -> UiEventEnvelope:
    """Broadcast the canonical task activity payload for one card."""
    state_reader = get_state_reader()
    payload = activity or _task_activity_response(state_reader, card_id)
    return broadcast_ui_event("task.activity.updated", payload.model_dump(mode="json"))


def broadcast_mcp_snapshot(items: list[WidgetResponse] | None = None) -> UiEventEnvelope:
    """Broadcast the current MCP server list to /api/ui/stream subscribers.

    Emitted after the Tools page requests the list and whenever an MCP config
    change is detected (backend edit, or runtime/agent write surfaced via the
    Postgres NOTIFY watcher), so the Tools page reflects changes live. Callers
    that already built the list (e.g. the GET handler) can pass it in to avoid
    a second ``get_widgets()`` pass and keep the snapshot consistent with the
    HTTP response.
    """
    source = _mcp_widget_items() if items is None else items
    payload = [item.model_dump(mode="json") for item in source]
    return broadcast_ui_event("mcp.snapshot", {"items": payload})


def broadcast_knowledge_snapshot(
    bases: list[dict[str, Any]] | None = None,
) -> UiEventEnvelope:
    """Broadcast the current knowledge-base list to /api/ui/stream subscribers.

    Emitted after backend-side knowledge mutations and whenever the ``knowledge``
    state document changes (UI ``PUT`` or an agent edit to ``/state/knowledge.json``
    surfaced via the Postgres NOTIFY watcher), so the Knowledge page reflects
    changes live without refetching. Callers that already built the list can pass
    it in to avoid a second snapshot query and keep the event consistent with the
    HTTP response.
    """
    payload = snapshot_knowledge_bases() if bases is None else bases
    return broadcast_ui_event("knowledge.snapshot", {"bases": payload})


def broadcast_skills_snapshot(
    skills: list[SkillFileResponse] | None = None,
) -> UiEventEnvelope:
    """Broadcast the current skill list to /api/ui/stream subscribers.

    Emitted after the Skills page requests the list and after UI-driven skill
    edits/creates, so the Skills page (and other open tabs) reflect changes live.
    Callers that already built the list can pass it in to avoid a second
    filesystem scan and keep the event consistent with the HTTP response.
    """
    if skills is None:
        skills = [
            SkillFileResponse.model_validate(skill, from_attributes=True)
            for skill in SkillService().list_skills()
        ]
    payload = [item.model_dump(mode="json") for item in skills]
    return broadcast_ui_event("skills.snapshot", {"skills": payload})


def broadcast_prompts_snapshot(
    prompts: list[PromptFileResponse] | None = None,
) -> UiEventEnvelope:
    """Broadcast the current prompt list to /api/ui/stream subscribers.

    Emitted after the Prompts page requests the list and after UI-driven prompt
    edits, so the Prompts page (and other open tabs) reflect changes live.
    Callers that already built the list can pass it in to avoid a second
    filesystem scan and keep the event consistent with the HTTP response.
    """
    if prompts is None:
        prompts = [
            PromptFileResponse.model_validate(prompt, from_attributes=True)
            for prompt in PromptService().list_prompts()
        ]
    payload = [item.model_dump(mode="json") for item in prompts]
    return broadcast_ui_event("prompts.snapshot", {"prompts": payload})


def broadcast_workflows_snapshot(
    items: list[WorkflowListItem] | None = None,
) -> UiEventEnvelope:
    """Broadcast the current workflow definition list."""

    if items is None:
        items = list_workflow_list_items(get_default_document_store())
    payload = [item.model_dump(mode="json") for item in items]
    return broadcast_ui_event("workflows.snapshot", {"items": payload})


def broadcast_workflow_detail(workflow_id: str) -> UiEventEnvelope:
    """Broadcast one workflow definition update or delete event."""

    definition = get_workflow_definition(get_default_document_store(), workflow_id)
    if definition is None:
        return broadcast_ui_event("workflow.deleted", {"workflow_id": workflow_id})
    payload = _workflow_content_response(definition).model_dump(mode="json", by_alias=True)
    return broadcast_ui_event("workflow.updated", payload)


def broadcast_workflow_runs_snapshot(
    runs: list[WorkflowRun] | None = None,
) -> UiEventEnvelope:
    """Broadcast the current workflow runs list."""

    if runs is None:
        runs = list_workflow_runs(get_default_document_store())
    payload = [item.model_dump(mode="json") for item in runs]
    return broadcast_ui_event("workflow.runs.snapshot", {"items": payload})


def broadcast_widgets_snapshot(items: list[WidgetResponse] | None = None) -> UiEventEnvelope:
    """Broadcast the unified widget list (dashboard/cross-cutting views).

    Callers holding a freshly built list can pass it in to avoid recomputing
    widgets and to keep the SSE snapshot consistent with their HTTP response.
    """
    if items is None:
        state_reader = get_state_reader()
        items = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    payload = [item.model_dump(mode="json") for item in items]
    return broadcast_ui_event("widgets.updated", {"items": payload})


def _broadcast_chat_sessions_snapshot(include_archived: bool = True):
    return broadcast_chat_sessions_snapshot(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
        include_archived=include_archived,
    )


router.include_router(
    create_chat_router(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
        get_current_state=lambda: get_state_reader().get_progenitor_state(),
    )
)


router.include_router(
    create_agent_profile_router(
        get_agent_store=get_agent_store,
        broadcast_ui_event=broadcast_ui_event,
    )
)


router.include_router(
    create_inbox_router(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
    )
)


def _broadcast_inbox_snapshot():
    return broadcast_inbox_snapshot(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
    )


def broadcast_chat_event(event_type: str, data: dict):
    """
    Broadcast an event to /events SSE subscribers.

    Use for IPC-related events like:
    - scheduled_response
    - agent_message
    - agent_question
    - alert
    - task
    - agent

    Args:
        event_type: Type of event
        data: Event data as JSON-serializable dict
    """
    event = {"type": event_type, "data": json.dumps(data)}
    broadcast_ui_event(event_type, data, scope="chat")

    dead: list[asyncio.Queue] = []
    for queue in _chat_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for chat subscriber — evicting dead connection")
            dead.append(queue)
    for q in dead:
        _chat_subscribers.discard(q)


def broadcast_status_event(event_type: str, data: Mapping[str, object]):
    """
    Broadcast an event to /status SSE subscribers.

    Use for status/health events like:
    - status
    - heartbeat

    Does NOT broadcast to /events (chat stream).

    Args:
        event_type: Type of event
        data: Event data as JSON-serializable dict
    """
    event = {"type": event_type, "data": json.dumps(data)}
    mapped_type = {
        "status": "status.updated",
        "heartbeat": "status.heartbeat",
        "widgets": "widgets.updated",
        "alerts": "alerts.updated",
    }.get(event_type, event_type)
    broadcast_ui_event(mapped_type, data)

    dead: list[asyncio.Queue] = []
    for queue in _status_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for status subscriber — evicting dead connection")
            dead.append(queue)
    for q in dead:
        _status_subscribers.discard(q)


def broadcast_alert(alert: AlertInfo):
    """Broadcast a new alert to all subscribers."""
    # Alerts go to chat stream
    broadcast_chat_event(
        "alert",
        {
            "id": alert.id,
            "type": alert.type,
            "title": alert.title,
            "message": alert.message,
            "urgent": alert.urgent,
        },
    )


def broadcast_status_change(status: str, details: dict | None = None):
    """Broadcast a status change to all subscribers."""
    # Status changes go to status stream
    broadcast_status_event(
        "status", {"status": status, "details": details or {}, "timestamp": time.time()}
    )


def broadcast_alerts_state() -> None:
    """Broadcast full alert payload updates to status subscribers."""
    broadcast_status_event("alerts", _alerts_payload())
