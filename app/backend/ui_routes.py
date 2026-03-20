"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.

Reads real state data from data/state/ and data/config/:
- health.json: Agent health status
- agents.json + mcp.json: Registered agents and tools
- build_state.json: Agent builds ONLY (inner loop, LLM messages, verification)
- current_state.json: Progenitor agent state (chat history, tool calls) - user session
- schedule.json: Scheduled tasks (system feature, persists beyond user sessions)
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

from croniter import croniter  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.progenitor.runtime.scheduler import Scheduler
from app.progenitor.runtime.time_utils import utcnow

from .prompt_service import PromptService
from .state_reader import get_state_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])


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
    progenitor_status: str = "unknown"  # Raw status from health.json
    uptime_seconds: float
    version: str
    memory_mb: float | None = None
    active_requests: int
    rate_limit_minute_limit: int
    rate_limit_hour_limit: int
    # State data
    cycle_count: int = 0
    run_id: str | None = None
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


class PromptUpdateRequest(BaseModel):
    """Prompt update payload."""

    path: str
    content: str


class WidgetPatchRequest(BaseModel):
    """Patch request for mutable backend-backed widgets."""
    enabled: bool | None = None
    name: str | None = None
    instruction: str | None = None
    schedule_type: str | None = None
    expression: str | None = None
    timezone: str | None = None


class ScheduleCreateRequest(BaseModel):
    """Create request for a new schedule entry."""
    name: str
    instruction: str
    schedule_type: str = "cron"
    expression: str
    timezone: str | None = None
    enabled: bool = True


class ActionRunRequest(BaseModel):
    """Run an action using the existing IPC message flow."""
    widget_id: str | None = None
    prompt: str | None = None
    code: str | None = None
    content: str | None = None
    input: str | None = None
    language: str | None = None


class ActionGenerateRequest(BaseModel):
    """Generate content using the existing IPC message flow."""
    widget_id: str | None = None
    prompt: str
    target: str | None = None


class ActionResponse(BaseModel):
    """Action response payload."""
    ok: bool
    widget_id: str | None = None
    response: str


class TaskInfo(BaseModel):
    """Active task information."""
    id: str
    title: str
    status: str
    description: str | None = None
    progress: int | None = None


class TasksResponse(BaseModel):
    """Response containing active tasks."""
    tasks: list[TaskInfo]
    total: int


class AlertInfo(BaseModel):
    """Alert information."""
    id: str
    type: str
    title: str
    message: str
    timestamp: float
    urgent: bool = False
    requires_response: bool = False


class AlertsResponse(BaseModel):
    """Response containing pending alerts."""
    alerts: list[AlertInfo]
    total: int


# In-memory alert storage (would be replaced with proper storage in production)
_pending_alerts: list[AlertInfo] = []
_alert_id_counter = 0


def generate_alert_id() -> str:
    """Generate a unique alert ID."""
    global _alert_id_counter
    _alert_id_counter += 1
    return f"alert-{int(time.time())}-{_alert_id_counter}"


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
            progenitor_status=snapshot.health.status,  # Raw status from health.json
            uptime_seconds=health.get("uptime_seconds", 0),
            version=health.get("version", "unknown"),
            memory_mb=health.get("memory", {}).get("rss_mb"),
            active_requests=health.get("shutdown", {}).get("active_requests", 0),
            rate_limit_minute_limit=health.get("rate_limiter", {}).get("requests_per_minute_limit", 60),
            rate_limit_hour_limit=health.get("rate_limiter", {}).get("requests_per_hour_limit", 500),
            # State data
            cycle_count=snapshot.health.cycle_count,
            run_id=snapshot.health.run_id,
            messages_count=len(state_reader.get_progenitor_state().get("messages", [])),
            pid=snapshot.health.pid,
            last_active=snapshot.health.last_active,
            # Build status
            build=build_info,
            # Lists
            loops=[
                LoopInfo(name=loop_item.name, status=loop_item.status, current_task=loop_item.current_task)
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
                BacklogItemInfo(id=b.id, type=b.item_type, status=b.status, description=b.description)
                for b in snapshot.backlog
            ],
            schedule=[
                ScheduleItemInfo(id=s.id, name=s.name, schedule=s.schedule, next_run=s.next_run, enabled=s.enabled)
                for s in snapshot.schedule
            ],
            widgets=state_reader.get_widgets(),
            sessions={
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
                "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
            },
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
            LoopInfo(name=loop_item.name, status=loop_item.status, current_task=loop_item.current_task)
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
            agent_infos.append(SubAgentInfo(
                name=agent.name,
                status="ready" if progenitor_running else "offline",
                kind=agent.kind,
                version=agent.version,
                description=agent.description,
                transport=agent.transport,
                task=None,
                progress=None,
            ))

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


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule():
    """
    Get scheduled items from schedule.json.
    """
    state_reader = get_state_reader()
    schedule = state_reader.get_schedule()

    return ScheduleResponse(
        items=[
            ScheduleItemInfo(
                id=s.id,
                name=s.name,
                schedule=s.schedule,
                next_run=s.next_run,
                enabled=s.enabled,
            )
            for s in schedule
        ],
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


def _widget_schemas() -> list[WidgetSchemaResponse]:
    """Return schema definitions for workspace details editing."""
    return [
        WidgetSchemaResponse(
            type="prompt",
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
                        WidgetFieldSchemaResponse(key="associatedAgents", label="Associated Agents", control="list"),
                        WidgetFieldSchemaResponse(key="linkedOutputIds", label="Linked Outputs", control="list"),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="code",
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
                        WidgetFieldSchemaResponse(key="runtimeProfile", label="Runtime Profile", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Content",
                    fields=[
                        WidgetFieldSchemaResponse(key="content", label="Code", control="textarea", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="execution",
                    label="Execution",
                    default_expanded=False,
                    fields=[
                        WidgetFieldSchemaResponse(key="lastRunId", label="Last Run"),
                        WidgetFieldSchemaResponse(key="outputRefs", label="Outputs", control="list"),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="webframe",
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
                        WidgetFieldSchemaResponse(key="html", label="HTML", control="textarea", editable=True),
                        WidgetFieldSchemaResponse(key="css", label="CSS", control="textarea", editable=True),
                        WidgetFieldSchemaResponse(key="js", label="JavaScript", control="textarea", editable=True),
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
                        WidgetFieldSchemaResponse(key="text", label="Text", control="textarea", editable=True),
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
                        WidgetFieldSchemaResponse(key="description", label="Description", control="textarea", editable=True),
                        WidgetFieldSchemaResponse(key="childIds", label="Children", control="list"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="schedule",
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
                        WidgetFieldSchemaResponse(key="enabled", label="Enabled", control="boolean", editable=True),
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
                        WidgetFieldSchemaResponse(key="expression", label="Expression", editable=True),
                        WidgetFieldSchemaResponse(key="timezone", label="Timezone", editable=True),
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="content",
                    label="Task",
                    fields=[
                        WidgetFieldSchemaResponse(key="instruction", label="Instruction", control="textarea", editable=True),
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
            type="agent",
            default_tab="overview",
            tabs=[
                WidgetTabSchemaResponse(id="overview", label="Overview"),
                WidgetTabSchemaResponse(id="output", label="Output"),
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
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    fields=[
                        WidgetFieldSchemaResponse(key="agentStatus", label="Status"),
                        WidgetFieldSchemaResponse(key="currentRunId", label="Run ID"),
                        WidgetFieldSchemaResponse(key="summaryState", label="Summary", control="textarea"),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
        WidgetSchemaResponse(
            type="mcp",
            default_tab="overview",
            tabs=[
                WidgetTabSchemaResponse(id="overview", label="Overview"),
                WidgetTabSchemaResponse(id="output", label="Output"),
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
                    ],
                ),
                WidgetSectionSchemaResponse(
                    id="status",
                    label="Status",
                    fields=[
                        WidgetFieldSchemaResponse(key="agentStatus", label="Status"),
                        WidgetFieldSchemaResponse(key="currentRunId", label="Run ID"),
                        WidgetFieldSchemaResponse(key="summaryState", label="Summary", control="textarea"),
                        WidgetFieldSchemaResponse(key="updatedAt", label="Updated"),
                    ],
                ),
            ],
        ),
    ]


def _atomic_write_json(filepath: Path, data: object) -> None:
    """Atomically write JSON for backend-backed state updates."""
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


def _broadcast_widget_refresh(state_reader) -> None:
    """Notify status listeners that backend-derived widgets changed."""
    broadcast_status_event("widgets", {"items": state_reader.get_widgets()})


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


def _resolve_schedule_expression(schedule_type: str, expression: str) -> tuple[str, str]:
    normalized_type = (schedule_type or "cron").strip().lower()
    normalized_expression = expression.strip()
    if not normalized_expression:
        raise HTTPException(status_code=400, detail="Schedule expression is required")

    if normalized_type == "cron":
        try:
            croniter(normalized_expression, utcnow())
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid cron expression") from exc
        return normalized_type, normalized_expression

    if normalized_type == "interval":
        return normalized_type, _interval_to_cron(normalized_expression)

    if normalized_type == "once":
        run_at = _parse_once_datetime(normalized_expression)
        cron_expression = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"
        return normalized_type, cron_expression

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
    normalized_type, cron_expression = _resolve_schedule_expression(schedule_type, expression)
    entry = {
        "id": schedule_id,
        "name": name,
        "message": instruction,
        "instruction": instruction,
        "user_request": name,
        "schedule": expression,
        "schedule_type": normalized_type,
        "cron_expression": cron_expression,
        "timezone": timezone_name or "UTC",
        "enabled": enabled,
        "last_run": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
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


@router.get("/widgets", response_model=WidgetsResponse)
async def get_widgets():
    """Get unified widget data derived from existing state/config/log files."""
    state_reader = get_state_reader()
    widgets = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    return WidgetsResponse(items=widgets, total=len(widgets))


@router.get("/widget-schemas", response_model=WidgetSchemasResponse)
async def get_widget_schemas():
    """Get schema metadata for the workspace details pane."""
    return WidgetSchemasResponse(schemas=_widget_schemas())


@router.patch("/widgets/{widget_id}", response_model=WidgetResponse)
async def patch_widget(widget_id: str, body: WidgetPatchRequest):
    """Patch a backend-owned widget where a matching state file exists."""
    state_reader = get_state_reader()
    if not widget_id.startswith("schedule:"):
        raise HTTPException(status_code=400, detail="Only schedule widgets are mutable from the backend")

    schedule_id = widget_id.split(":", 1)[1]
    schedule_path = state_reader.state_dir / "schedule.json"
    schedule_data = state_reader._read_json("schedule.json")  # noqa: SLF001 - shared helper
    if not isinstance(schedule_data, list):
        raise HTTPException(status_code=404, detail="Schedule state not found")

    updated = None
    for entry in schedule_data:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") != schedule_id:
            continue
        current_schedule_type = body.schedule_type or entry.get("schedule_type") or "cron"
        current_expression = body.expression or entry.get("schedule") or entry.get("cron_expression") or ""
        if body.enabled is not None:
            entry["enabled"] = body.enabled
        if body.name is not None:
            entry["name"] = body.name
            entry["user_request"] = body.name
        if body.instruction is not None:
            entry["message"] = body.instruction
            entry["instruction"] = body.instruction
        if body.expression is not None or body.schedule_type is not None:
            normalized_type, cron_expression = _resolve_schedule_expression(current_schedule_type, current_expression)
            entry["schedule_type"] = normalized_type
            entry["schedule"] = current_expression
            entry["cron_expression"] = cron_expression
        if body.timezone is not None:
            entry["timezone"] = body.timezone
        entry["updated_at"] = utcnow().isoformat()
        next_run = Scheduler().next_run_for_entry(entry)
        entry["next_run"] = next_run.isoformat() if next_run else None
        entry["next_run_at"] = next_run.isoformat() if next_run else None
        updated = entry
        break

    if updated is None:
        raise HTTPException(status_code=404, detail="Widget not found")

    _atomic_write_json(schedule_path, schedule_data)
    state_reader.invalidate(schedule_path)

    refreshed = next((widget for widget in state_reader.get_widgets() if widget["id"] == widget_id), None)
    if refreshed is None:
        refreshed = {
            "id": widget_id,
            "type": "schedule",
            "name": updated.get("name", schedule_id),
            "title": updated.get("name", schedule_id),
            "status": "enabled" if updated.get("enabled", True) else "disabled",
            "description": updated.get("message"),
            "summary": updated.get("message"),
            "logs": [],
            "updated_at": updated.get("created_at"),
        }

    _broadcast_widget_refresh(state_reader)
    return _widget_to_response(refreshed)


@router.get("/schedules", response_model=ScheduleResponse)
async def get_schedules():
    """Alias for schedule data using the requested plural path."""
    return await get_schedule()


@router.post("/schedules", response_model=WidgetResponse)
async def create_schedule(body: ScheduleCreateRequest):
    """Create a new schedule entry by writing directly to schedule.json."""
    name = body.name.strip()
    instruction = body.instruction.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Schedule name is required")
    if not instruction:
        raise HTTPException(status_code=400, detail="Schedule task is required")

    state_reader = get_state_reader()
    schedule_path = state_reader.state_dir / "schedule.json"
    schedule_data = state_reader._read_json("schedule.json")  # noqa: SLF001 - shared helper
    if schedule_data is None:
        schedule_data = []
    if not isinstance(schedule_data, list):
        raise HTTPException(status_code=500, detail="Schedule state is invalid")

    created_at = utcnow().isoformat()
    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
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
    schedule_data.append(entry)

    _atomic_write_json(schedule_path, schedule_data)
    state_reader.invalidate(schedule_path)

    refreshed = next((widget for widget in state_reader.get_widgets() if widget["id"] == f"schedule:{schedule_id}"), None)
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
    return _widget_to_response(refreshed)


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

    response = await chat_main.client.send_message("\n\n".join(segments))
    broadcast_chat_event("agent_message", {
        "content": response,
        "metadata": {"widget_id": body.widget_id, "action": "run"},
        "timestamp": time.time(),
    })
    _broadcast_widget_refresh(get_state_reader())
    return ActionResponse(ok=True, widget_id=body.widget_id, response=response)


@router.post("/actions/generate", response_model=ActionResponse)
async def action_generate(body: ActionGenerateRequest):
    """Generate output through the existing IPC message flow."""
    from . import main as chat_main

    if not chat_main.client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    prompt = body.prompt
    if body.target:
        prompt = f"Target: {body.target}\n\n{prompt}"
    if body.widget_id:
        prompt = f"Widget ID: {body.widget_id}\n\n{prompt}"

    response = await chat_main.client.send_message(prompt)
    broadcast_chat_event("agent_message", {
        "content": response,
        "metadata": {"widget_id": body.widget_id, "action": "generate"},
        "timestamp": time.time(),
    })
    _broadcast_widget_refresh(get_state_reader())
    return ActionResponse(ok=True, widget_id=body.widget_id, response=response)


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
        web_clients_active=subscriber_counts["chat"],
        will_forward_to_telegram=(
            telegram is not None
            and telegram._started
            and subscriber_counts["chat"] == 0
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
            success=False,
            message="Telegram integration not enabled or not configured"
        )

    try:
        success = await telegram.send_message(
            "🧪 Test message from Progenitor Backend\n\n"
            "If you see this, Telegram integration is working!"
        )
        if success:
            return TelegramTestResponse(
                success=True,
                message="Test message sent successfully"
            )
        else:
            return TelegramTestResponse(
                success=False,
                message="Failed to send test message - check logs for details"
            )
    except Exception as e:
        logger.error("Telegram test failed: %s", e)
        return TelegramTestResponse(
            success=False,
            message=f"Error: {str(e)}"
        )


@router.get("/tasks", response_model=TasksResponse)
async def get_active_tasks():
    """
    Get list of active tasks.

    Returns information about tasks currently being processed.
    """
    from . import main as chat_main

    try:
        health = await chat_main.health_check_detailed()
        active_requests = health.get("shutdown", {}).get("active_requests", 0)
    except Exception:
        active_requests = 0

    # In a full implementation, this would query the progenitor for task status
    tasks = []

    if active_requests > 0:
        tasks.append(TaskInfo(
            id="task-active-1",
            title="Processing Chat Request",
            status="running",
            description=f"Handling {active_requests} active request(s)",
            progress=None
        ))

    return TasksResponse(tasks=tasks, total=len(tasks))


@router.get("/alerts", response_model=AlertsResponse)
async def get_alerts():
    """
    Get pending alerts that require user attention.

    Returns alerts generated by the progenitor agent.
    """
    return AlertsResponse(alerts=_pending_alerts, total=len(_pending_alerts))


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str):
    """
    Dismiss an alert.

    Removes the alert from the pending list.
    """
    global _pending_alerts

    original_count = len(_pending_alerts)
    _pending_alerts = [a for a in _pending_alerts if a.id != alert_id]

    if len(_pending_alerts) == original_count:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {"status": "dismissed", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/respond")
async def respond_to_alert(alert_id: str, response: str = "acknowledged"):
    """
    Respond to an alert.

    Acknowledges the alert and optionally sends a response back to the agent.
    """
    global _pending_alerts

    alert = next((a for a in _pending_alerts if a.id == alert_id), None)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Remove from pending
    _pending_alerts = [a for a in _pending_alerts if a.id != alert_id]

    # In a full implementation, this would send the response to the progenitor
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
    prompts = [PromptFileResponse.model_validate(prompt, from_attributes=True) for prompt in service.list_prompts(agent=agent)]
    return PromptsResponse(prompts=prompts, total=len(prompts))


@router.get("/prompts/content", response_model=PromptFileResponse)
async def get_prompt_content(path: str = Query(..., min_length=1)):
    """Get a single prompt file from the mounted prompts directory."""
    try:
        return PromptFileResponse.model_validate(PromptService().get_prompt(path), from_attributes=True)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/prompts/content", response_model=PromptFileResponse)
async def update_prompt_content(request: PromptUpdateRequest):
    """Persist edits to an existing prompt file."""
    try:
        return PromptFileResponse.model_validate(
            PromptService().save_prompt(request.path, request.content),
            from_attributes=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{request.path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/logs")
async def get_logs(
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    limit: int = 500,
    format: str = "text",
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
    entries = state_reader.get_logs(level=level, search=search, source=source, limit=limit)

    if format == "json":
        return {
            "entries": entries,
            "total": len(entries),
            "filtered": level is not None or search is not None or source is not None,
        }

    content = "\n".join(
        f"[{entry['time']}] [{entry['level'].upper()}] [{entry['source']}] {entry['message']}"
        for entry in entries
    )
    return {"logs": content, "total": len(entries)}


def create_alert(
    type: str,
    title: str,
    message: str,
    urgent: bool = False,
    requires_response: bool = False
) -> AlertInfo:
    """
    Create a new alert (called by the progenitor agent).

    Args:
        type: Alert type (info, warning, error)
        title: Alert title
        message: Alert message
        urgent: Whether the alert is urgent
        requires_response: Whether the alert requires a user response

    Returns:
        Created AlertInfo
    """
    alert = AlertInfo(
        id=generate_alert_id(),
        type=type,
        title=title,
        message=message,
        timestamp=time.time(),
        urgent=urgent,
        requires_response=requires_response
    )
    _pending_alerts.append(alert)

    # Keep only last 100 alerts
    while len(_pending_alerts) > 100:
        _pending_alerts.pop(0)

    return alert


# Event subscribers for SSE - split into chat and status streams
_chat_subscribers: list[asyncio.Queue] = []  # For IPC events (chat, tasks, alerts)
_status_subscribers: list[asyncio.Queue] = []  # For state file status updates

# Shutdown event for SSE connections
_sse_shutdown_event: asyncio.Event | None = None

# Status monitor state
_status_monitor_task: asyncio.Task | None = None
_last_status: dict = {}
_state_watch_callback = None
_state_watch_push_task: asyncio.Task | None = None

# Track active SSE response tasks so we can cancel them on shutdown
_active_chat_tasks: list[asyncio.Task] = []
_active_status_tasks: list[asyncio.Task] = []


def has_active_chat_subscribers() -> bool:
    """Check if there are any active SSE chat subscribers (web UI connected).

    Used by Telegram integration to determine whether to send messages to Telegram.
    If there are active chat subscribers, messages go to web UI and not Telegram.

    Returns:
        True if at least one chat subscriber is connected.
    """
    return len(_chat_subscribers) > 0


def get_subscriber_counts() -> dict[str, int]:
    """Get counts of all subscriber types.

    Returns:
        Dictionary with chat and status subscriber counts.
    """
    return {
        "chat": len(_chat_subscribers),
        "status": len(_status_subscribers),
        "total": len(_chat_subscribers) + len(_status_subscribers),
    }


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""
    from . import main as chat_main

    state_reader = get_state_reader()
    snapshot = state_reader.get_snapshot()

    now = time.time()
    start_time = getattr(chat_main, '_server_start_time', now)
    shutdown_mgr = getattr(chat_main, 'shutdown_manager', None)

    status = {
        "status": "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": snapshot.health.is_running(),
        "progenitor_status": snapshot.health.status,  # Raw status from health.json
        "version": getattr(chat_main.config, 'api_version', '0.1.0'),
        # State data
        "cycle_count": snapshot.health.cycle_count,  # Use health.cycle_count for accuracy
        "run_id": snapshot.health.run_id or snapshot.run_id,
        "messages_count": snapshot.messages_count,
        "pid": snapshot.health.pid,
        "last_active": snapshot.health.last_active,
        # Loops
        "loops": [
            {"name": loop_item.name, "status": loop_item.status, "current_task": loop_item.current_task}
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
        } if snapshot.build else None,
        "widgets": state_reader.get_widgets(),
        "sessions": {
            "total": snapshot.sessions.total if snapshot.sessions else 0,
            "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
            "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
        },
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


async def _status_monitor():
    """Background task that polls status and broadcasts to SSE subscribers.

    Pushes status updates every 3 seconds for responsive UI.
    Pushes to _status_subscribers for status stream consumers.
    """
    global _last_status

    update_interval = 3  # Push status every 3 seconds for responsive updates

    logger.info("[StatusMonitor] Starting status monitor loop")

    while True:
        try:
            await asyncio.sleep(update_interval)

            # Count subscribers
            status_count = len(_status_subscribers)
            total_subscribers = status_count

            if total_subscribers == 0:
                continue  # No subscribers, skip

            current = await _get_current_status()
            _last_status = current.copy()

            # Push to /status subscribers
            broadcast_status_event("status", current)

            logger.debug(f"[StatusMonitor] Pushed status to {status_count} status subscribers")

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
    finally:
        _state_watch_push_task = None


def _queue_state_watch_status(_snapshot) -> None:
    global _state_watch_push_task
    if not _status_subscribers:
        return
    if _state_watch_push_task and not _state_watch_push_task.done():
        return
    _state_watch_push_task = asyncio.create_task(_broadcast_latest_status())


async def start_state_status_watcher(interval: float = 1.0) -> None:
    global _state_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is None:
        _state_watch_callback = _queue_state_watch_status
        state_reader.add_watcher(_state_watch_callback)
    await state_reader.start_watching(interval)


async def stop_state_status_watcher() -> None:
    global _state_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is not None:
        state_reader.remove_watcher(_state_watch_callback)
        _state_watch_callback = None
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
    global _sse_shutdown_event, _active_chat_tasks, _active_status_tasks

    # Count all subscriber types
    chat_count = len(_chat_subscribers)
    status_count = len(_status_subscribers)
    total_count = chat_count + status_count

    logger.info("Closing SSE connections: %d chat, %d status (total: %d)",
                chat_count, status_count, total_count)

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = get_sse_shutdown_event()
    shutdown_event.set()

    # Send shutdown event to all subscriber types
    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}'
    }

    for queue in _chat_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    for queue in _status_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    all_tasks = _active_chat_tasks + _active_status_tasks
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

    logger.info("SSE shutdown signal sent to all connections")


# ============================================================================
# NEW SPLIT SSE ENDPOINTS
# ============================================================================

async def _generate_chat_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for chat/IPC stream only.

    This stream receives:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages
    - alert: System alerts
    - task: Task status updates
    - agent: Agent activity updates

    Does NOT receive status/health updates (use /status for those).
    """

    queue: asyncio.Queue = asyncio.Queue()
    _chat_subscribers.append(queue)

    # Track current task for cancellation during shutdown
    current_task = asyncio.current_task()
    if current_task:
        _active_chat_tasks.append(current_task)

    # Get shutdown event to watch for server shutdown
    shutdown_event = get_sse_shutdown_event()

    try:
        # Send initial connection event
        yield f'event: connected\ndata: {{"stream": "chat", "timestamp": {time.time()}}}\n\n'

        while True:
            # Check if shutdown has been requested
            if shutdown_event.is_set():
                logger.debug("Chat SSE connection closing due to server shutdown")
                yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                break

            try:
                # Wait for event with timeout (30s keepalive)
                event = await asyncio.wait_for(queue.get(), timeout=30.0)

                # Check if this is a shutdown event
                if event.get('type') == 'shutdown':
                    yield f"event: shutdown\ndata: {event['data']}\n\n"
                    break

                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except TimeoutError:
                # Check shutdown again on timeout
                if shutdown_event.is_set():
                    yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                    break
                # Send heartbeat (no status payload - just keepalive)
                yield f'event: heartbeat\ndata: {{"timestamp": {time.time()}}}\n\n'
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            yield 'event: shutdown\ndata: {"reason": "connection_cancelled", "message": "Connection closed"}\n\n'
    finally:
        if queue in _chat_subscribers:
            _chat_subscribers.remove(queue)
        if current_task and current_task in _active_chat_tasks:
            _active_chat_tasks.remove(current_task)


async def _generate_status_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for status/health stream only.

    This stream receives:
    - status: Full status updates from state files
    - heartbeat: Periodic heartbeats with status

    Does NOT receive chat/IPC events (use /chat for those).
    """
    import json

    queue: asyncio.Queue = asyncio.Queue()
    _status_subscribers.append(queue)

    # Track current task for cancellation during shutdown
    current_task = asyncio.current_task()
    if current_task:
        _active_status_tasks.append(current_task)

    # Get shutdown event to watch for server shutdown
    shutdown_event = get_sse_shutdown_event()

    # Start status monitor if not running
    start_status_monitor()

    try:
        # Send initial connection event with current status
        initial_status = await _get_current_status()
        yield f"event: connected\ndata: {json.dumps(initial_status)}\n\n"

        while True:
            # Check if shutdown has been requested
            if shutdown_event.is_set():
                logger.debug("Status SSE connection closing due to server shutdown")
                yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                break

            try:
                # Wait for event with timeout (5s for responsive status updates)
                event = await asyncio.wait_for(queue.get(), timeout=5.0)

                # Check if this is a shutdown event
                if event.get('type') == 'shutdown':
                    yield f"event: shutdown\ndata: {event['data']}\n\n"
                    break

                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except TimeoutError:
                # Check shutdown again on timeout
                if shutdown_event.is_set():
                    yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                    break
                # Send heartbeat with current status
                heartbeat_status = await _get_current_status()
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat_status)}\n\n"
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            yield 'event: shutdown\ndata: {"reason": "connection_cancelled", "message": "Connection closed"}\n\n'
    finally:
        if queue in _status_subscribers:
            _status_subscribers.remove(queue)
        if current_task and current_task in _active_status_tasks:
            _active_status_tasks.remove(current_task)


@router.get("/events")
async def events_stream():
    """
    Server-Sent Events endpoint for IPC events.

    Connect to receive real-time notifications about:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages (talk to user)
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
    - alert: {"id": "...", "type": "...", "title": "...", "message": "...", "urgent": false}
    """
    return StreamingResponse(
        _generate_chat_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/status")
async def status_stream():
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
    return StreamingResponse(
        _generate_status_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


# ============================================================================
# BROADCAST FUNCTIONS
# ============================================================================

def broadcast_chat_event(event_type: str, data: dict):
    """
    Broadcast an event to /chat SSE subscribers.

    Use for IPC-related events like:
    - scheduled_response
    - agent_message
    - alert
    - task
    - agent

    Args:
        event_type: Type of event
        data: Event data as JSON-serializable dict
    """
    import json

    event = {
        "type": event_type,
        "data": json.dumps(data)
    }

    # Push to /chat subscribers
    for queue in _chat_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for chat subscriber")


def broadcast_status_event(event_type: str, data: dict):
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
    import json

    event = {
        "type": event_type,
        "data": json.dumps(data)
    }

    # Push to /status subscribers only
    for queue in _status_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for status subscriber")


def broadcast_alert(alert: AlertInfo):
    """Broadcast a new alert to all subscribers."""
    # Alerts go to chat stream
    broadcast_chat_event("alert", {
        "id": alert.id,
        "type": alert.type,
        "title": alert.title,
        "message": alert.message,
        "urgent": alert.urgent
    })


def broadcast_status_change(status: str, details: dict | None = None):
    """Broadcast a status change to all subscribers."""
    # Status changes go to status stream
    broadcast_status_event("status", {
        "status": status,
        "details": details or {},
        "timestamp": time.time()
    })
