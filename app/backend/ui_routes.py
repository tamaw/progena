"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.

Reads state from the shared Postgres document store used by both the backend
and Progenitor runtime. The backend exposes state-document changes to the
frontend through canonical UI events:
- health: persisted heartbeat status plus a live Progenitor reachability check
- registry and MCP: registered agents, runtime tools, and enabled MCP servers
- schedules, knowledge, workflows, kanban, and inbox: Postgres-backed documents

The Progenitor agent sees selected documents as validated virtual files such as
`/config/agents.json`, `/config/mcp.json`, and `/config/schedule.json`. These names
are not local filesystem paths; `build_state.json` and `current_state.json` are
not backend-owned files in the current runtime.
"""

import asyncio
import contextlib
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import httpx
from croniter import croniter
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from app.progenitor.config import default_plugins_config_payload
from app.progenitor.content_documents import AGENT_DOCUMENT_KEY_PREFIX, SKILL_DOCUMENT_KEY_PREFIX
from app.progenitor.cron import cron_error, default_timezone_name, next_cron_time
from app.progenitor.document_kinds import (
    KANBAN_CARD_KEY_PREFIX,
    KANBAN_TASK_CONTENT_KEY_PREFIX,
    SESSION_KEY_PREFIX,
)
from app.progenitor.document_types import DOCUMENT_OPERATIONS, DocumentType
from app.progenitor.routines.store import ROUTINE_GRAPH_KEY_PREFIX
from app.progenitor.schedule import Scheduler
from app.progenitor.schedule.service import generate_schedule_id
from app.progenitor.utils import utcnow
from app.progenitor.workflows import (
    WORKFLOW_DEFAULT_DOCUMENT_TYPE,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
    WorkflowDefinition,
    WorkflowListItem,
    WorkflowRun,
    default_workflow_definition,
    document_create_input_schema,
    document_create_output_schema,
    document_delete_input_schema,
    document_delete_output_schema,
    document_event_output_schema,
    document_query_output_schema,
    document_read_input_schema,
    document_read_output_schema,
    document_write_input_schema,
    document_write_output_schema,
    parse_workflow_invoke_request_document,
    workflow_id_from_path,
)

from .agent_catalog_routes import (
    AgentGraphModel,
    broadcast_agent_profiles_snapshot,
    create_agent_profile_router,
)
from .agent_store import get_agent_store
from .alerts import (
    AlertInfo,
    MarkAlertsViewedRequest,
    get_alert,
    get_alerts_response,
    mark_alerts_viewed,
)
from .alerts import (
    create_alert as store_alert,
)
from .chat_routes import broadcast_chat_sessions_snapshot, create_chat_router
from .chat_sessions import (
    UiEventEnvelope,
    UiRequestAcknowledgement,
    acknowledgement_response,
    get_chat_summary,
)
from .document_events import (
    DocumentEventDispatcher,
    TypedDocumentEvent,
    list_document_types,
)
from .document_store import (
    BACKEND_STATE_PREFIX,
    PROGENITOR_STATE_PREFIX,
    DocumentEvent,
    document_write_origin,
    get_default_document_store,
)
from .inbox_routes import (
    INBOX_MESSAGE_KEY_PREFIX,
    broadcast_inbox_snapshot,
    create_inbox_router,
)
from .knowledge_service import snapshot_bases as snapshot_knowledge_bases
from .marketplace_service import load_marketplaces
from .model_profile_routes import create_model_profile_router
from .policy_routes import create_policy_router
from .preferences_service import load_preferences
from .prompt_service import PromptService
from .repo_service import merged_repo_detail, merged_repo_rows, workspace_signal
from .request_utils import request_correlation_id as _request_correlation_id
from .routine_events import routine_events
from .state_reader import StateReader, get_state_reader
from .threads import complete_thread, fail_thread, upsert_thread
from .workflow_cron import WorkflowCronClock
from .workflow_service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    delete_workflow_definition,
    get_workflow_definition,
    get_workflow_run,
    list_workflow_definitions,
    list_workflow_list_items,
    list_workflow_runs,
    save_workflow_definition,
)

SOURCE_UI = "ui"
SOURCE_WORKFLOW = "workflow"

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])


@router.get("/workflows/cron-preview")
async def preview_workflow_cron(
    expression: str,
    timezone: str,
    count: int = Query(default=3, ge=1, le=10),
) -> dict[str, Any]:
    """Next due times of a cron trigger, computed exactly as the backend clock fires them."""
    error = cron_error(expression, timezone)
    if error:
        raise HTTPException(status_code=400, detail=error)
    runs: list[str] = []
    cursor = utcnow()
    for _ in range(count):
        cursor = next_cron_time(expression, cursor, timezone)
        runs.append(cursor.isoformat())
    return {"next_runs": runs}


MAX_EVENT_WIDGET_LOGS = 20


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
    """Selectable option for a kanban card field."""

    value: str
    label: str


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


class PromptSummaryResponse(BaseModel):
    """Workspace prompt metadata without the file body (list payload)."""

    path: str
    filename: str
    directory: str
    category: str | None = None
    primary_agent: str | None = None
    associated_agents: list[str] = Field(default_factory=list)
    size_bytes: int
    updated_at: float
    preview: str


class PromptsResponse(BaseModel):
    """Response containing discovered prompts."""

    prompts: list[PromptSummaryResponse]
    total: int


class PromptUpdateRequest(BaseModel):
    """Prompt update payload."""

    path: str
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
    """Run an action using the existing SSE message flow."""

    widget_id: str | None = None
    prompt: str | None = None
    code: str | None = None
    content: str | None = None
    input: str | None = None
    language: str | None = None
    broadcast_response: bool = True
    thread_id: str | None = None
    invocation_type: Literal["user", "automation", "system", "workflow_step"] = "automation"
    invocation_source: str | None = None
    root_widget_id: str | None = None


class ActionGenerateRequest(BaseModel):
    """Generate content using the existing SSE message flow."""

    widget_id: str | None = None
    prompt: str
    input: str | None = None
    target: str | None = None
    thread_id: str | None = None
    invocation_type: Literal["user", "automation", "system", "workflow_step"] = "automation"
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
    document_type: str = WORKFLOW_DEFAULT_DOCUMENT_TYPE


class WorkflowContentPutRequest(BaseModel):
    """Persist one workflow definition."""

    path: str | None = None
    workflow: dict[str, Any]


class WorkflowManualTriggerRequest(BaseModel):
    """Manual trigger payload for operator testing, merged over neutral event defaults."""

    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowApprovalRequest(BaseModel):
    """Resolve a paused workflow approval."""

    approved: bool


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
    invocation_type: Literal["user", "automation", "system", "workflow_step"],
    invocation_source: str | None,
    widget_id: str | None,
    root_widget_id: str | None,
    action: str,
    workflow_run_id: str | None = None,
) -> dict[str, str]:
    """Build metadata sent through the backend→Progenitor boundary."""

    metadata = {
        "thread_id": thread_id,
        "invocation_type": invocation_type,
        "invocation_source": invocation_source or action,
        "action": action,
        "source": SOURCE_WORKFLOW if invocation_type == "workflow_step" else SOURCE_UI,
    }
    if widget_id:
        metadata["widget_id"] = widget_id
    if root_widget_id:
        metadata["root_widget_id"] = root_widget_id
    if workflow_run_id:
        metadata["workflow_run_id"] = workflow_run_id
    return metadata


_workflow_executor: WorkflowExecutor | None = None


def _workflow_event_request_id(payload: Mapping[str, Any]) -> str | None:
    """Recover a finite UI request correlation stored on a workflow trigger."""
    trigger = payload.get("trigger_event")
    request_id = trigger.get("request_id") if isinstance(trigger, Mapping) else None
    return request_id if isinstance(request_id, str) else None


async def _workflow_prompt_runner(
    *,
    workflow: WorkflowDefinition,
    run_id: str,
    node_id: str,
    node,
    input_text: str,
    thread_id: str | None,
) -> PromptExecutionResult:
    """Execute every agent/extract workflow step through one normal prompt path.

    ``input_text`` is the rendered prompt (plus any appended instructions);
    it is the sole instruction sent to the model.
    """

    widget_id = f"workflow:{workflow.id}:{node_id}"
    request = ActionRunRequest(
        widget_id=widget_id,
        input=input_text,
        broadcast_response=False,
        thread_id=thread_id,
        invocation_type="workflow_step",
        invocation_source=widget_id,
        root_widget_id=f"workflow:{workflow.id}",
    )
    thread_id, _response_obj, response_text = await _execute_action_run(
        request, workflow_run_id=run_id
    )
    return PromptExecutionResult(response=response_text, thread_id=thread_id)


def get_workflow_executor() -> WorkflowExecutor:
    """Return the singleton backend workflow executor."""
    global _workflow_executor
    if _workflow_executor is None:
        _workflow_executor = WorkflowExecutor(
            store_getter=get_default_document_store,
            prompt_runner=_workflow_prompt_runner,
            emit_event=lambda event_type, payload: broadcast_ui_event(
                event_type,
                payload,
                request_id=_workflow_event_request_id(payload),
            ),
        )
    return _workflow_executor


_workflow_cron_clock: WorkflowCronClock | None = None


def get_workflow_cron_clock() -> WorkflowCronClock:
    """Return the singleton clock firing workflow cron trigger nodes."""
    global _workflow_cron_clock
    if _workflow_cron_clock is None:
        _workflow_cron_clock = WorkflowCronClock(
            store_getter=get_default_document_store,
            definitions_getter=list_workflow_definitions,
            executor_getter=get_workflow_executor,
        )
    return _workflow_cron_clock


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


_KANBAN_CARD_KEY_PREFIX = KANBAN_CARD_KEY_PREFIX


def _kanban_card_store_key(card_id: str) -> str:
    """Return the shared-store key for one kanban card document."""
    return f"{_KANBAN_CARD_KEY_PREFIX}{card_id}"


def _append_kanban_card_diff_activity(
    state_reader: StateReader,
    *,
    previous_card: dict,
    refreshed_card: dict,
    now: str,
) -> None:
    """Record task-activity entries and broadcast them for one card's before/after diff."""
    card_id = str(refreshed_card.get("id") or "").strip()
    if not card_id:
        return
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


def _task_content_store_key(card_id: str) -> str:
    """Return the shared-store key for one task body."""
    return f"{KANBAN_TASK_CONTENT_KEY_PREFIX}{card_id}"


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
                "source": entry.get("source", "document"),
                "message": entry.get("message") or "",
            }
        )
    return normalized[:MAX_EVENT_WIDGET_LOGS]


def _normalize_event_widget_record(entry: dict) -> dict:
    event_type = str(entry.get("event_type") or "kanban_card.created").strip().lower()
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
    document = state_reader.document_store.get_document(BACKEND_STATE_PREFIX, "event_widgets", {})
    raw = document.payload if document is not None else {}
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
    normalized = {
        entry_id: _normalize_event_widget_record({**entry, "id": entry.get("id") or entry_id})
        for entry_id, entry in records.items()
        if isinstance(entry, dict)
    }
    state_reader.document_store.save_document(BACKEND_STATE_PREFIX, "event_widgets", normalized)
    return normalized


def _validate_event_type(value: str) -> str:
    """Validate an event widget name: ``<document_type>.<op>``."""

    normalized = (value or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="Event type is required")
    type_id, _, op = normalized.rpartition(".")
    if op not in DOCUMENT_OPERATIONS or type_id not in list_document_types(
        get_default_document_store()
    ):
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


def _read_mcp_config(state_reader: StateReader) -> dict:
    """Read the live MCP config from the shared state document."""
    return state_reader.get_mcp_config()


def _write_mcp_config(state_reader: StateReader, data: dict[str, Any]) -> None:
    """Persist the MCP config to the shared state document."""
    state_reader.save_mcp_config(data)


def _read_schedule_config(state_reader: StateReader) -> dict[str, Any]:
    """Read the live schedule config, keyed by schedule id."""
    document = state_reader.document_store.get_document(PROGENITOR_STATE_PREFIX, "schedule", {})
    raw = document.payload if document is not None else {}
    return raw if isinstance(raw, dict) else {}


def _delete_schedule_entry(state_reader: StateReader, schedule_id: str) -> bool:
    """Delete a persisted schedule entry and refresh the cached schedule document."""
    schedule_data = _read_schedule_config(state_reader)

    def _save() -> None:
        state_reader.document_store.save_document(
            PROGENITOR_STATE_PREFIX, "schedule", schedule_data
        )

    return _delete_document_entry(entries=schedule_data, entry_id=schedule_id, save=_save)


def _broadcast_widget_refresh(
    state_reader: StateReader,
    *,
    request_id: str | None = None,
    refreshed_widget: dict[str, Any] | None = None,
) -> None:
    """Notify status listeners that backend-derived widgets changed.

    A live agent inspection is ephemeral: it comes from the agent card rather
    than a state document.  Merge it into this event's snapshot so the UI can
    consume the inspection without fabricating a local response body.
    """
    items = state_reader.get_widgets()
    if refreshed_widget is not None:
        items = [
            refreshed_widget if item.get("id") == refreshed_widget.get("id") else item
            for item in items
        ]
    broadcast_ui_event("widgets.updated", {"items": items}, request_id=request_id)


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


def document_event_name(document_type_id: str, op: str) -> str:
    """Event widget name for one document event, e.g. ``kanban_card.created``."""

    return f"{document_type_id}.{op}"


def _summarize_document_event(typed: TypedDocumentEvent) -> str:
    label = typed.document_type.label if typed.document_type else typed.event.key
    document = typed.event.after if typed.event.after is not None else typed.event.before
    title = str(document.get("title") or "").strip() if isinstance(document, dict) else ""
    suffix = f" for {title}" if title else ""
    return f"{label} {typed.event.op}{suffix}"


async def _apply_document_event_widgets(typed: TypedDocumentEvent) -> None:
    """Count document events on every enabled event widget subscribed to them."""

    if typed.document_type is None or typed.output is None:
        return
    state_reader = get_state_reader()
    event_widgets = _read_event_widget_records(state_reader)
    if not event_widgets:
        return
    name = document_event_name(typed.document_type.id, typed.event.op)
    timestamp = typed.event.created_at.isoformat()
    summary = _summarize_document_event(typed)
    payload = {
        field: typed.output[field]
        for field in ("event_id", "document_type", "key", "id", "op", "changed_fields")
    }
    changed = False
    for widget in event_widgets.values():
        if not widget.get("enabled", True) or widget.get("event_type") != name:
            continue
        widget["trigger_count"] = int(widget.get("trigger_count", 0) or 0) + 1
        widget["last_triggered_at"] = timestamp
        widget["last_run_status"] = "success"
        widget["last_payload"] = payload
        widget["summary"] = summary
        widget["updated_at"] = timestamp
        widget["logs"] = [
            {
                "time": timestamp,
                "timestamp": timestamp,
                "level": "info",
                "source": typed.document_type.id,
                "message": summary,
            },
            *_normalize_event_widget_logs(widget.get("logs")),
        ][:MAX_EVENT_WIDGET_LOGS]
        changed = True
    if changed:
        _write_event_widget_records(state_reader, event_widgets)
        _broadcast_widget_refresh(state_reader)


_workflow_invoke_lock = asyncio.Lock()


async def _reconcile_workflow_invoke_requests(changed_key: str | None = None) -> None:
    """Consume durable workflow invocation requests from Progenitor."""

    if changed_key is not None and not changed_key.startswith(WORKFLOW_INVOKE_REQUEST_KEY_PREFIX):
        return
    store = get_default_document_store()
    rows = store.list_documents(PROGENITOR_STATE_PREFIX, WORKFLOW_INVOKE_REQUEST_KEY_PREFIX)
    if not rows:
        return
    executor = get_workflow_executor()
    async with _workflow_invoke_lock:
        for key, document in rows:
            payload = document.payload if isinstance(document.payload, dict) else {}
            try:
                request = parse_workflow_invoke_request_document(payload)
            except Exception:
                logger.warning("Dropping malformed workflow invoke request %r", key, exc_info=True)
                store.delete_document(PROGENITOR_STATE_PREFIX, key)
                continue
            try:
                definition = get_workflow_definition(store, request.workflow_id)
                if definition is not None:
                    # The request input fills the trigger node's declared output
                    # ports (e.g. an event node's ``key``/``after``), exactly
                    # like an on-demand run, and is validated against them.
                    await executor.trigger_workflow(
                        request.workflow_id,
                        build_manual_trigger_event(definition, payload=request.input),
                        source="progenitor",
                        ancestry=request.ancestry,
                    )
            except Exception:
                logger.exception(
                    "Workflow invoke request %r failed; retaining it for retry",
                    request.request_id,
                )
                continue
            store.delete_document(PROGENITOR_STATE_PREFIX, key)


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


def _workflow_content_response(definition: WorkflowDefinition) -> WorkflowContentResponse:
    return WorkflowContentResponse(
        path=f"/workflows/{definition.id}.json",
        workflow=definition,
    )


def _workflow_event_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return _workflow_content_response(definition).model_dump(mode="json", by_alias=True)


@router.get("/workflows", status_code=202, response_model=UiRequestAcknowledgement)
async def get_workflows(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Return the workflow definition list for the workflows page/sidebar."""
    request_id = _request_correlation_id(request, x_request_id)
    items = list_workflow_list_items(get_default_document_store())
    broadcast_ui_event(
        "workflows.snapshot",
        {"items": [item.model_dump(mode="json") for item in items]},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflows.snapshot"])


def _document_type_snapshot_item(document_type: DocumentType) -> dict[str, Any]:
    """Describe one document type with every port schema a workflow editor needs."""

    document_schema = document_type.load_schema()
    return {
        "id": document_type.id,
        "label": document_type.label,
        "key_prefix": document_type.key_prefix,
        "key": document_type.key,
        "collection": document_type.collection,
        "schema": document_type.schema_name,
        "document_schema": document_schema,
        "event_output_schema": document_event_output_schema(document_type, document_schema),
        "read_input_schema": document_read_input_schema(document_type),
        "read_output_schema": document_read_output_schema(document_type, document_schema),
        "query_output_schema": document_query_output_schema(document_type, document_schema),
        "writable_fields": list(document_type.writable_fields),
        "write_input_schema": document_write_input_schema(document_type, document_schema)
        if document_type.writable_fields
        else None,
        "write_output_schema": document_write_output_schema(document_type, document_schema)
        if document_type.writable_fields
        else None,
        "create_input_schema": document_create_input_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
        "create_output_schema": document_create_output_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
        "delete_input_schema": document_delete_input_schema()
        if document_type.collection and document_schema is not None
        else None,
        "delete_output_schema": document_delete_output_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
    }


@router.get("/document-types", status_code=202, response_model=UiRequestAcknowledgement)
async def get_document_types(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Publish the document type registry (``document_type:<id>`` rows).

    Backs the workflow editor's document type pickers; each item carries the
    exact port schemas the server derives for event/read/query nodes on save.
    """
    request_id = _request_correlation_id(request, x_request_id)
    types = list_document_types(get_default_document_store())
    items = [
        _document_type_snapshot_item(item)
        for item in sorted(types.values(), key=lambda item: item.label.lower())
    ]
    broadcast_ui_event("document_types.snapshot", {"items": items}, request_id=request_id)
    return acknowledgement_response(request_id, ["document_types.snapshot"])


@router.get("/workflows/content", status_code=202, response_model=UiRequestAcknowledgement)
async def get_workflow_content(
    request: Request,
    path: str = Query(...),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Return one stored workflow definition by id or mounted virtual path."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        definition = get_workflow_definition(get_default_document_store(), path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    payload = _workflow_content_response(definition)
    broadcast_ui_event(
        "workflow.updated", payload.model_dump(mode="json", by_alias=True), request_id=request_id
    )
    return acknowledgement_response(request_id, ["workflow.updated"])


@router.put("/workflows/content", status_code=202, response_model=UiRequestAcknowledgement)
async def put_workflow_content(
    body: WorkflowContentPutRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Persist one workflow definition."""
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_ui_event(
        "workflow.updated", payload.model_dump(mode="json", by_alias=True), request_id=request_id
    )
    broadcast_ui_event(
        "workflows.snapshot",
        {
            "items": [
                item.model_dump(mode="json")
                for item in list_workflow_list_items(get_default_document_store())
            ]
        },
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflow.updated", "workflows.snapshot"])


@router.post("/workflows", status_code=202, response_model=UiRequestAcknowledgement)
async def create_workflow(
    body: WorkflowCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Create a new workflow definition with the minimal valid scaffold."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        definition = save_workflow_definition(
            get_default_document_store(),
            default_workflow_definition(
                title=body.title.strip() or "Untitled workflow",
                description=body.description,
                document_type=body.document_type,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _workflow_content_response(definition)
    broadcast_ui_event(
        "workflow.updated", payload.model_dump(mode="json", by_alias=True), request_id=request_id
    )
    broadcast_ui_event(
        "workflows.snapshot",
        {
            "items": [
                item.model_dump(mode="json")
                for item in list_workflow_list_items(get_default_document_store())
            ]
        },
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflow.updated", "workflows.snapshot"])


@router.delete("/workflows/{workflow_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def delete_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Delete one stored workflow definition."""
    request_id = _request_correlation_id(request, x_request_id)
    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    delete_workflow_definition(store, workflow_id)
    broadcast_ui_event("workflow.deleted", {"workflow_id": workflow_id}, request_id=request_id)
    broadcast_ui_event(
        "workflows.snapshot",
        {"items": [item.model_dump(mode="json") for item in list_workflow_list_items(store)]},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflow.deleted", "workflows.snapshot"])


@router.post(
    "/workflows/{workflow_id}/pause", status_code=202, response_model=UiRequestAcknowledgement
)
async def pause_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Pause one workflow definition and any active run."""
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_ui_event(
        "workflow.updated", payload.model_dump(mode="json", by_alias=True), request_id=request_id
    )
    broadcast_workflows_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["workflow.updated", "workflows.snapshot"])


@router.post(
    "/workflows/{workflow_id}/resume", status_code=202, response_model=UiRequestAcknowledgement
)
async def resume_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Resume one paused workflow definition and its active run, if any."""
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_ui_event(
        "workflow.updated", payload.model_dump(mode="json", by_alias=True), request_id=request_id
    )
    broadcast_workflows_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["workflow.updated", "workflows.snapshot"])


@router.get("/workflow-runs", status_code=202, response_model=UiRequestAcknowledgement)
async def get_workflow_runs(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Return active and recent workflow runs."""
    request_id = _request_correlation_id(request, x_request_id)
    items = list_workflow_runs(get_default_document_store())
    broadcast_workflow_runs_snapshot(items, request_id=request_id)
    return acknowledgement_response(request_id, ["workflow.runs.snapshot"])


@router.post(
    "/workflows/{workflow_id}/trigger", status_code=202, response_model=UiRequestAcknowledgement
)
async def trigger_workflow(
    workflow_id: str,
    body: WorkflowManualTriggerRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Manually trigger one workflow definition for operator testing."""
    request_id = _request_correlation_id(request, x_request_id)
    store = get_default_document_store()
    try:
        definition = get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        trigger = build_manual_trigger_event(definition, payload=body.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger = {**trigger, "request_id": request_id}
    run = await get_workflow_executor().trigger_workflow(definition.id, trigger)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Workflow trigger refused because the workflow is paused or disabled",
        )
    refreshed = get_workflow_run(store, run.run_id) or run
    run_payload = refreshed.model_dump(mode="json")
    broadcast_ui_event(
        "workflow.run.started",
        {
            "workflow_id": workflow_id,
            "run_id": refreshed.run_id,
            "status": refreshed.status,
            "run": run_payload,
        },
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflow.run.started"])


@router.post(
    "/workflows/{workflow_id}/runs/{run_id}/approval",
    status_code=202,
    response_model=UiRequestAcknowledgement,
)
async def resolve_workflow_approval(
    workflow_id: str,
    run_id: str,
    body: WorkflowApprovalRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Approve or reject a paused workflow approval node."""

    request_id = _request_correlation_id(request, x_request_id)
    run = await get_workflow_executor().resolve_approval(
        workflow_id,
        run_id,
        approved=body.approved,
    )
    if run is None:
        raise HTTPException(status_code=409, detail="Workflow run is not awaiting approval")
    broadcast_ui_event(
        "workflow.run.updated",
        {"workflow_id": workflow_id, "run_id": run_id, "run": run.model_dump(mode="json")},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["workflow.run.updated"])


def _mcp_widget_items() -> list[WidgetResponse]:
    """The MCP-type widgets, used by the /mcp route and the mcp.snapshot event.

    List payload strips per-tool schemas and log history (``data.tools``,
    ``.logs``) — the list/filter/sort UI never reads them; only the selected
    server's detail view does, fetched on demand via ``GET /mcp/content``.
    """
    state_reader = get_state_reader()
    items = []
    for widget in state_reader.get_widgets():
        if widget.get("type") != "mcp":
            continue
        response = _widget_to_response(widget)
        lean_data = {
            key: value for key, value in response.data.items() if key not in ("tools", "logs")
        }
        items.append(response.model_copy(update={"data": lean_data, "logs": []}))
    return items


def _broadcast_mcp_server_detail(widget_id: str, *, request_id: str | None = None) -> bool:
    """Re-publish one MCP server's full detail (tools + logs) on the UI stream.

    Returns ``False`` (no broadcast) when ``widget_id`` no longer names a live
    MCP widget, so callers can 404 or silently skip as appropriate.
    """
    state_reader = get_state_reader()
    widget = next(
        (
            w
            for w in state_reader.get_widgets()
            if w.get("type") == "mcp" and w.get("id") == widget_id
        ),
        None,
    )
    if widget is None:
        return False
    response = _widget_to_response(widget)
    broadcast_ui_event(
        "mcp.server.detail.updated", response.model_dump(mode="json"), request_id=request_id
    )
    return True


@router.get("/widgets", status_code=202, response_model=UiRequestAcknowledgement)
async def get_widgets(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Return the unified widget list and publish it on the UI stream.

    Mirrors the schedule/agent/mcp pattern: the GET acknowledges and broadcasts
    ``widgets.updated`` so pages render from the realtime store rather than
    holding the HTTP response themselves.
    """
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    widgets = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    broadcast_widgets_snapshot(widgets, request_id=request_id)
    return acknowledgement_response(request_id, ["widgets.updated"])


@router.get("/mcp", status_code=202, response_model=UiRequestAcknowledgement)
async def get_mcp_servers(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Acknowledge MCP-list requests and publish the snapshot on the UI stream.

    Mirrors the schedule/agent pattern: a dedicated typed event (``mcp.snapshot``)
    feeds a dedicated store slice, so the Tools page renders from the realtime
    store instead of filtering the generic widget list.
    """
    request_id = _request_correlation_id(request, x_request_id)
    items = _mcp_widget_items()
    broadcast_mcp_snapshot(items, request_id=request_id)
    return acknowledgement_response(request_id, ["mcp.snapshot"])


@router.get("/mcp/content", status_code=202, response_model=UiRequestAcknowledgement)
async def get_mcp_server_detail(
    id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Acknowledge a request for one MCP server's full detail and publish it.

    Mirrors ``GET /workflows/content``/``GET /prompts/content``: the list
    (``mcp.snapshot``) omits per-tool schemas and log history; the selected
    server's full body (tools + logs) is fetched on demand here.
    """
    request_id = _request_correlation_id(request, x_request_id)
    if not _broadcast_mcp_server_detail(id, request_id=request_id):
        raise HTTPException(status_code=404, detail=f"MCP server '{id}' not found")
    return acknowledgement_response(request_id, ["mcp.server.detail.updated"])


@router.post(
    "/widgets/{widget_id}/refresh", status_code=202, response_model=UiRequestAcknowledgement
)
async def refresh_widget(
    widget_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Refresh agent widget metadata from its live protocol surface.

    MCP servers are not refreshed here: their tools/capabilities are discovered
    by the Progenitor runtime when it initialises its MCP context (and on
    ``mcp_refresh``), which is the single source of truth the UI reads from.
    """
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    widget = _find_widget_payload(state_reader, widget_id)

    if widget.get("type") == "agent":
        refreshed = await _inspect_agent_widget(widget, state_reader)
        _broadcast_widget_refresh(
            state_reader,
            request_id=request_id,
            refreshed_widget=refreshed,
        )
        return acknowledgement_response(request_id, ["widgets.updated"])

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


@router.patch("/widgets/{widget_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def patch_widget(
    widget_id: str,
    body: WidgetPatchRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Patch a backend-owned widget where a matching state file exists."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    if widget_id.startswith("schedule:"):
        schedule_id = widget_id.split(":", 1)[1]
        schedule_data = _read_schedule_config(state_reader)

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
            state_reader.document_store.save_document(
                PROGENITOR_STATE_PREFIX, "schedule", schedule_data
            )

        updated = _patch_document_entry(
            entries=schedule_data,
            entry_id=schedule_id,
            apply=_apply_schedule_patch,
            save=_save_schedule,
        )

        _widget_or_fallback(
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

        _broadcast_widget_refresh(state_reader, request_id=request_id)
        broadcast_schedule_snapshot(request_id=request_id)
        return acknowledgement_response(request_id, ["widgets.updated"])

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

        _widget_or_fallback(
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

        _broadcast_widget_refresh(state_reader, request_id=request_id)
        return acknowledgement_response(request_id, ["widgets.updated"])

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
        _broadcast_widget_refresh(state_reader, request_id=request_id)
        broadcast_mcp_snapshot(request_id=request_id)
        event_types = ["widgets.updated", "mcp.snapshot"]
        server_name_for_tool = str(target.get("server") or "").strip()
        if server_name_for_tool and _broadcast_mcp_server_detail(
            f"mcp:{server_name_for_tool}", request_id=request_id
        ):
            event_types.append("mcp.server.detail.updated")
        return acknowledgement_response(request_id, event_types)

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

    _widget_or_fallback(
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

    _broadcast_widget_refresh(state_reader, request_id=request_id)
    broadcast_mcp_snapshot(request_id=request_id)
    event_types = ["widgets.updated", "mcp.snapshot"]
    if _broadcast_mcp_server_detail(widget_id, request_id=request_id):
        event_types.append("mcp.server.detail.updated")
    return acknowledgement_response(request_id, event_types)


@router.delete("/widgets/{widget_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def delete_widget(
    widget_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Delete a backend-owned widget where a matching state file exists."""
    request_id = _request_correlation_id(request, x_request_id)
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
            logger.warning("MCP widget %s was already removed before delete request", widget_id)
            _broadcast_widget_refresh(state_reader, request_id=request_id)
            broadcast_mcp_snapshot(request_id=request_id)
            return acknowledgement_response(request_id, ["widgets.updated", "mcp.snapshot"])
        _broadcast_widget_refresh(state_reader, request_id=request_id)
        broadcast_mcp_snapshot(request_id=request_id)
        return acknowledgement_response(request_id, ["widgets.updated", "mcp.snapshot"])

    if not widget_id.startswith("schedule:"):
        raise HTTPException(
            status_code=400,
            detail="Only schedule and MCP widgets can be deleted from the backend",
        )

    schedule_id = widget_id.split(":", 1)[1]
    deleted = _delete_schedule_entry(state_reader, schedule_id)
    if not deleted:
        logger.warning("Schedule widget %s was already removed before delete request", widget_id)
        _broadcast_widget_refresh(state_reader, request_id=request_id)
        broadcast_schedule_snapshot(request_id=request_id)
        return acknowledgement_response(request_id, ["widgets.updated", "schedule.snapshot"])

    _broadcast_widget_refresh(state_reader, request_id=request_id)
    broadcast_schedule_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["widgets.updated", "schedule.snapshot"])


@router.get("/schedules", status_code=202, response_model=UiRequestAcknowledgement)
async def get_schedules(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Alias for schedule data using the requested plural path."""
    request_id = _request_correlation_id(request, x_request_id)
    broadcast_schedule_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["schedule.snapshot"])


@router.post("/schedules", status_code=202, response_model=UiRequestAcknowledgement)
async def create_schedule(
    body: ScheduleCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Create a new schedule entry in backend state."""
    name = body.name.strip()
    instruction = body.instruction.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Schedule name is required")
    if not instruction:
        raise HTTPException(status_code=400, detail="Schedule task is required")
    request_id = _request_correlation_id(request, x_request_id)

    state_reader = get_state_reader()
    schedule_data = _read_schedule_config(state_reader)

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

    state_reader.document_store.save_document(PROGENITOR_STATE_PREFIX, "schedule", schedule_data)

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

    _broadcast_widget_refresh(state_reader, request_id=request_id)
    broadcast_schedule_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["schedule.snapshot"])


@router.post(
    "/schedules/{schedule_id}/trigger", status_code=202, response_model=UiRequestAcknowledgement
)
async def retrigger_schedule(
    schedule_id: str,
    body: ScheduleTriggerRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Reschedule a task from a natural-language description.

    Rather than editing cron fields by hand, the user describes the timing in
    plain language ("friday at 15:00"). We hand that to the Progenitor agent on a
    dedicated (non-chat) thread carrying just the schedule id and the free text -
    the loaded ``managing-schedules`` skill owns how a natural-language reschedule request
    gets interpreted and applied to ``/config/schedule.json``, not a hardcoded
    tool-call prescription here. We then recompute ``next_run`` (in case the
    agent didn't) and rebroadcast a snapshot.
    """
    from . import main as chat_main

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Trigger description is required")
    if not chat_main.client:
        raise HTTPException(status_code=503, detail="Progenitor client not initialized")
    request_id = _request_correlation_id(request, x_request_id)

    state_reader = get_state_reader()
    schedule_data = _read_schedule_config(state_reader)
    entry = schedule_data.get(schedule_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Schedule not found")

    instruction = (
        f'Reschedule the task with id "{schedule_id}" under /config/schedule.json to run: '
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
    schedule_data = _read_schedule_config(state_reader)
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
        state_reader.document_store.save_document(
            PROGENITOR_STATE_PREFIX, "schedule", schedule_data
        )

    _broadcast_widget_refresh(state_reader, request_id=request_id)
    broadcast_schedule_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["schedule.snapshot"])


@router.post("/events", status_code=202, response_model=UiRequestAcknowledgement)
async def create_event_widget(
    body: EventWidgetCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Create a new persisted event widget trigger."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Event widget name is required")
    request_id = _request_correlation_id(request, x_request_id)

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

    _broadcast_widget_refresh(state_reader, request_id=request_id)
    return acknowledgement_response(request_id, ["widgets.updated"])


@router.get("/kanban", status_code=202, response_model=UiRequestAcknowledgement)
async def get_kanban_board(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Get the persisted kanban board used by the frontend."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    response = _kanban_board_response(state_reader, board)
    broadcast_kanban_snapshot(response, request_id=request_id)
    return acknowledgement_response(request_id, ["kanban.snapshot"])


@router.post("/kanban/cards", status_code=202, response_model=UiRequestAcknowledgement)
async def create_kanban_card(
    body: KanbanCardCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Create a new kanban card in the backlog column."""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Card title is required")
    request_id = _request_correlation_id(request, x_request_id)

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
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board), request_id=request_id)
    return acknowledgement_response(request_id, ["kanban.snapshot"])


@router.patch("/kanban/cards/{card_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def patch_kanban_card(
    card_id: str,
    body: KanbanCardPatchRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Patch an existing kanban card."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    column_map = {column["id"]: column for column in board["columns"]}
    allowed_statuses = {status.id for status in _kanban_status_options()}
    now = utcnow().isoformat()

    updated_card = None
    for card in board["cards"]:
        if card["id"] != card_id:
            continue

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
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board), request_id=request_id)
    return acknowledgement_response(request_id, ["kanban.snapshot"])


@router.delete("/kanban/cards/{card_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def delete_kanban_card(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Delete an existing kanban card."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    board = _read_kanban_board(state_reader)
    original_count = len(board["cards"])
    board["cards"] = [card for card in board["cards"] if card["id"] != card_id]
    if len(board["cards"]) == original_count:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    board = _write_kanban_board(state_reader, board)
    broadcast_kanban_snapshot(_kanban_board_response(state_reader, board), request_id=request_id)
    return acknowledgement_response(request_id, ["kanban.snapshot"])


@router.get("/tasks/{card_id}/activity", status_code=202, response_model=UiRequestAcknowledgement)
async def get_task_activity(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Acknowledge a task-activity request and publish its canonical timeline."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    _find_kanban_card(state_reader, card_id)
    activity = _task_activity_response(state_reader, card_id)
    broadcast_task_activity(card_id, activity, request_id=request_id)
    return acknowledgement_response(request_id, ["task.activity.updated"])


@router.get("/tasks/{card_id}/content", status_code=202, response_model=UiRequestAcknowledgement)
async def get_task_content(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Acknowledge a task-content request and publish its canonical markdown."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    card = _find_kanban_card(state_reader, card_id)
    content = _read_task_content(state_reader, card)
    broadcast_task_content(card_id, content, request_id=request_id)
    return acknowledgement_response(request_id, ["task.content.updated"])


@router.put("/tasks/{card_id}/content", status_code=202, response_model=UiRequestAcknowledgement)
async def update_task_content(
    card_id: str,
    body: TaskContentUpdateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Persist the markdown body for a single task card."""
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_task_content(card_id, result, request_id=request_id)
    broadcast_task_activity(card_id, request_id=request_id)
    return acknowledgement_response(request_id, ["task.content.updated", "task.activity.updated"])


@router.post("/tasks/{card_id}/activity", status_code=202, response_model=UiRequestAcknowledgement)
async def create_task_activity(
    card_id: str,
    body: TaskActivityCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Append a user-visible activity entry for a task card."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    _find_kanban_card(state_reader, card_id)
    _append_task_activity_entry(
        state_reader,
        card_id=card_id,
        kind=body.kind,
        actor=body.actor,
        message=body.message,
        metadata=body.metadata,
        created_at=utcnow().isoformat(),
    )
    broadcast_task_activity(card_id, request_id=request_id)
    return acknowledgement_response(request_id, ["task.activity.updated"])


async def _execute_action_run(
    body: ActionRunRequest, *, workflow_run_id: str | None = None
) -> tuple[str, Any, str]:
    """Run an action internally and return its thread ID, response, and text.

    Internal callers do not receive an HTTP acknowledgement envelope.
    """
    from . import main as chat_main

    if not chat_main.client:
        raise RuntimeError("Client not initialized")

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
        raise ValueError("No run payload provided")

    thread_id = _resolve_action_thread_id(body.thread_id)
    metadata = _build_action_metadata(
        thread_id=thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source,
        widget_id=body.widget_id,
        root_widget_id=body.root_widget_id,
        action=body.invocation_source or body.language or "workspace.run",
        workflow_run_id=workflow_run_id,
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
    return (thread_id, response, response.response)


@router.post("/actions/run", status_code=202, response_model=UiRequestAcknowledgement)
async def action_run(
    body: ActionRunRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Run a prompt/code action through the existing SSE message flow."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        thread_id, response_obj, response_text = await _execute_action_run(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent action failed: {exc}") from exc
    if body.broadcast_response:
        broadcast_ui_event(
            "agent_message",
            {
                "content": response_text,
                "metadata": {"widget_id": body.widget_id, "action": "run", "thread_id": thread_id},
                "timestamp": time.time(),
            },
            scope="chat",
            request_id=request_id,
        )
    _broadcast_widget_refresh(get_state_reader(), request_id=request_id)
    return acknowledgement_response(request_id, ["widgets.updated"])


@router.post("/actions/generate", status_code=202, response_model=UiRequestAcknowledgement)
async def action_generate(
    body: ActionGenerateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Generate output through the existing SSE message flow."""
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_ui_event(
        "action.generate.sent",
        {
            "thread_id": thread_id,
            "widget_id": body.widget_id,
            "response": response.response,
        },
        request_id=request_id,
    )
    broadcast_ui_event(
        "agent_message",
        {
            "content": response.response,
            "metadata": {"widget_id": body.widget_id, "action": "generate", "thread_id": thread_id},
            "timestamp": time.time(),
        },
        scope="chat",
        request_id=request_id,
    )
    _broadcast_widget_refresh(get_state_reader(), request_id=request_id)
    return acknowledgement_response(request_id, ["action.generate.sent", "widgets.updated"])


@router.get("/alerts", status_code=202, response_model=UiRequestAcknowledgement)
async def get_alerts(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """
    Get backend-managed alert history.

    Returns alerts generated by the progenitor agent.
    """
    request_id = _request_correlation_id(request, x_request_id)
    broadcast_alerts_state(request_id=request_id)
    return acknowledgement_response(request_id, ["alerts.updated"])


@router.post("/alerts/viewed", status_code=202, response_model=UiRequestAcknowledgement)
async def mark_viewed_alerts(
    request: Request,
    body: MarkAlertsViewedRequest | None = None,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Mark alerts viewed without removing them from history."""
    request_id = _request_correlation_id(request, x_request_id)
    mark_alerts_viewed(body.alert_ids if body else None)
    broadcast_alerts_state(request_id=request_id)
    return acknowledgement_response(request_id, ["alerts.updated"])


@router.post("/alerts/{alert_id}/dismiss", status_code=202, response_model=UiRequestAcknowledgement)
async def dismiss_alert(
    alert_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """
    Dismiss an alert.

    Alerts remain in history; dismissing only clears unread state.
    """
    request_id = _request_correlation_id(request, x_request_id)
    alert = get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    mark_alerts_viewed([alert_id])
    broadcast_alerts_state(request_id=request_id)
    return acknowledgement_response(request_id, ["alerts.updated"])


@router.post("/alerts/{alert_id}/respond", status_code=202, response_model=UiRequestAcknowledgement)
async def respond_to_alert(
    request: Request,
    alert_id: str,
    response: str = "acknowledged",
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """
    Respond to an alert.

    Acknowledges the alert and optionally sends a response back to the agent.
    """
    request_id = _request_correlation_id(request, x_request_id)
    alert = get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    mark_alerts_viewed([alert_id])
    broadcast_alerts_state(request_id=request_id)
    logger.info("Alert %s responded with: %s", alert_id, response)

    return acknowledgement_response(request_id, ["alerts.updated"])


@router.get("/prompts", status_code=202, response_model=UiRequestAcknowledgement)
async def get_prompts(
    request: Request,
    agent: str | None = None,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Publish the complete prompt catalogue; consumers filter locally."""
    request_id = _request_correlation_id(request, x_request_id)
    service = PromptService()
    # A catalogue snapshot is global canonical state. Never publish a
    # request-specific subset as a snapshot for all connected clients.
    prompts = [
        PromptSummaryResponse.model_validate(prompt, from_attributes=True)
        for prompt in service.list_prompts()
    ]
    broadcast_prompts_snapshot(prompts, request_id=request_id)
    return acknowledgement_response(request_id, ["prompts.snapshot"])


@router.get("/prompts/content", status_code=202, response_model=UiRequestAcknowledgement)
async def get_prompt_content(
    request: Request,
    path: str = Query(...),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Acknowledge a prompt-content request and publish its canonical body."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        record = PromptService().get_prompt(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = PromptFileResponse.model_validate(record, from_attributes=True)
    broadcast_ui_event(
        "prompt.content.updated", payload.model_dump(mode="json"), request_id=request_id
    )
    return acknowledgement_response(request_id, ["prompt.content.updated"])


@router.put("/prompts/content", status_code=202, response_model=UiRequestAcknowledgement)
async def update_prompt_content(
    body: PromptUpdateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Persist edits to an existing prompt file."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        saved = PromptFileResponse.model_validate(
            PromptService().save_prompt(body.path, body.content),
            from_attributes=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt '{body.path}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_ui_event(
        "prompt.content.updated", saved.model_dump(mode="json"), request_id=request_id
    )
    broadcast_prompts_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["prompt.content.updated", "prompts.snapshot"])


@router.get("/logs", status_code=202, response_model=UiRequestAcknowledgement)
async def get_logs(
    request: Request,
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
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
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
    request_id = _request_correlation_id(request, x_request_id)
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
    page_payload = {
        **result,
        "filters": {
            "level": level,
            "search": search,
            "source": source,
            "folder": folder,
            "file": file_path,
            "start": start_date,
            "end": end_date,
            "after": after,
            "limit": limit,
            "format": format,
            "search_mode": search_mode,
        },
        "active_selection": {
            "source": source or "",
            "group": folder or "",
            "file": file_path or "",
        },
        "date_range": {"start": start_date, "end": end_date},
        "cursor": {
            "after": after,
            "newest_cursor": result["newest_cursor"],
            "cursor_found": result["cursor_found"],
        },
        "source_groups": source_groups,
        "folders": state_reader.get_log_folders(),
    }
    broadcast_ui_event("logs.page.loaded", page_payload, request_id=request_id)
    return acknowledgement_response(request_id, ["logs.page.loaded"])


@router.get("/logs/sources", status_code=202, response_model=UiRequestAcknowledgement)
async def get_log_sources(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Return available log groups/files without loading log entries."""
    request_id = _request_correlation_id(request, x_request_id)
    state_reader = get_state_reader()
    source_groups = state_reader.get_log_sources()
    folders = state_reader.get_log_folders()
    broadcast_ui_event(
        "logs.sources.loaded",
        {"source_groups": source_groups, "folders": folders},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["logs.sources.loaded"])


@router.get("/logs/export", status_code=202, response_model=UiRequestAcknowledgement)
async def export_logs(
    request: Request,
    level: str | None = None,
    search: str | None = None,
    source: str | None = None,
    folder: str | None = None,
    file_path: str | None = Query(default=None, alias="file"),
    start_date: str | None = Query(default=None, alias="start"),
    end_date: str | None = Query(default=None, alias="end"),
    search_mode: str = "plain",
    format: str = "text",
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Export logs using the current filters.

    The export data is broadcast as chunked events (``logs.export.chunk``) on the
    UI event stream. The frontend reconstructs the download from these chunks.
    """
    request_id = _request_correlation_id(request, x_request_id)
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
    broadcast_ui_event(
        "logs.export.started", {"format": format, "total": len(entries)}, request_id=request_id
    )

    # Broadcast entries as chunked events (batches of 100)
    chunk_size = 100
    for i in range(0, len(entries), chunk_size):
        chunk = entries[i : i + chunk_size]
        broadcast_ui_event(
            "logs.export.chunk",
            {"format": format, "index": i // chunk_size, "entries": chunk, "total": len(entries)},
            request_id=request_id,
        )

    broadcast_ui_event(
        "logs.export.completed",
        {"format": format, "total": len(entries)},
        request_id=request_id,
    )

    event_types = ["logs.export.started"]
    if entries:
        event_types.append("logs.export.chunk")
    event_types.append("logs.export.completed")
    return acknowledgement_response(request_id, event_types)


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

# Event subscribers for the canonical SSE stream: queue -> handler task.
# The task reference lets eviction cancel a stalled handler so its HTTP
# connection actually closes (a full queue means the handler stopped draining,
# typically because it is blocked writing to a dead/half-open socket; leaving
# it open makes the client's EventSource permanently deaf with no reconnect).
_ui_stream_subscribers: dict[asyncio.Queue, asyncio.Task | None] = {}
_ui_stream_clients: dict[asyncio.Queue, str] = {}
_ui_event_seq = 0
_ui_event_replay: list[UiEventEnvelope] = []
_UI_EVENT_REPLAY_LIMIT = 200

# Shutdown event for SSE connections
_sse_shutdown_event: asyncio.Event | None = None

# Status monitor state
_status_monitor_task: asyncio.Task | None = None
_state_watch_callback = None
_state_watch_push_task: asyncio.Task | None = None

# Track active SSE response tasks so we can cancel them on shutdown
_active_ui_stream_tasks: set[asyncio.Task] = set()
# Keep a strong reference to fire-and-forget agent-profile broadcasts so the
# event loop's weak task reference can't let one get garbage-collected mid-run.
_active_agent_profile_broadcast_tasks: set[asyncio.Task] = set()
# Same for model-profile broadcasts.
_active_model_profile_broadcast_tasks: set[asyncio.Task] = set()


def has_active_ui_stream_subscribers() -> bool:
    """Check if there are any active canonical UI stream subscribers.

    Telegram fallback should follow the modern ``/api/ui/stream`` connection,
    not the legacy ``/api/ui/events`` subscriber list.
    """
    return len(_ui_stream_subscribers) > 0


def get_subscriber_counts() -> dict[str, int]:
    """Get count of active canonical stream subscribers."""
    n = len(_ui_stream_subscribers)
    return {"stream": n, "web": n, "total": n}


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

    Also includes ``progenitor_running``: a live SSE-heartbeat liveness check,
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

            if not _ui_stream_subscribers:
                continue  # No subscribers, skip

            broadcast_ui_event("status.updated", _process_metrics())

        except asyncio.CancelledError:
            print("[StatusMonitor] Cancelled", flush=True)
            break
        except Exception as e:
            print(f"[StatusMonitor] Error: {e}", flush=True)
            await asyncio.sleep(10)


async def _broadcast_latest_status() -> None:
    global _state_watch_push_task
    try:
        current = await _get_current_status()
        broadcast_ui_event("status.updated", current)
        if _ui_stream_subscribers:
            await _broadcast_chat_sessions_snapshot()
    finally:
        _state_watch_push_task = None


# Store keys whose change should refresh the unified widget list.
_WIDGET_KEYS = frozenset({"mcp", "mcp_tools", "schedule", "registry", "event_widgets"})


async def _broadcast_agent_profiles_safe() -> None:
    try:
        await broadcast_agent_profiles_snapshot(get_agent_store, broadcast_ui_event)
    except Exception:
        logger.debug("Failed to broadcast agent profiles snapshot", exc_info=True)


async def _broadcast_model_profiles_safe() -> None:
    try:
        from .model_profile_routes import broadcast_model_profiles_snapshot

        await broadcast_model_profiles_snapshot(get_default_document_store(), broadcast_ui_event)
    except Exception:
        logger.debug("Failed to broadcast model profiles snapshot", exc_info=True)


def _queue_state_watch_status(snapshot, changed_key: str | None = None) -> None:
    global _state_watch_push_task
    if not _ui_stream_subscribers:
        return
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
        if changed_key in (None, "preferences"):
            broadcast_preferences_snapshot()
        if changed_key in (None, "marketplaces"):
            broadcast_marketplaces_snapshot()
        if changed_key in (None, "plugins"):
            broadcast_plugins_snapshot()
        if changed_key in (None, "profile"):
            profile_task = asyncio.create_task(_broadcast_model_profiles_safe())
            _active_model_profile_broadcast_tasks.add(profile_task)
            profile_task.add_done_callback(_active_model_profile_broadcast_tasks.discard)
        if (
            changed_key is None
            or changed_key == "registry"
            or changed_key.startswith(AGENT_DOCUMENT_KEY_PREFIX)
            or changed_key.startswith(SKILL_DOCUMENT_KEY_PREFIX)
        ):
            profile_task = asyncio.create_task(_broadcast_agent_profiles_safe())
            _active_agent_profile_broadcast_tasks.add(profile_task)
            profile_task.add_done_callback(_active_agent_profile_broadcast_tasks.discard)
        if changed_key is None:
            board = _read_kanban_board(get_state_reader())
            for card in board["cards"]:
                card_id = str(card.get("id") or "").strip()
                if card_id:
                    broadcast_task_content(card_id)
                    broadcast_task_activity(card_id)
        elif changed_key.startswith("kanban_task_content:"):
            card_id = changed_key.split(":", 1)[1]
            broadcast_task_content(card_id)
            broadcast_task_activity(card_id)
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
        if changed_key in (None, "repos", "repo_scan"):
            broadcast_repositories_snapshot()
        if changed_key is None or changed_key in _WIDGET_KEYS:
            broadcast_widgets_snapshot()
    except Exception:
        logger.debug("Failed to broadcast dedicated snapshot", exc_info=True)
    if _state_watch_push_task and not _state_watch_push_task.done():
        return
    _state_watch_push_task = asyncio.create_task(_broadcast_latest_status())


_workflow_invoke_watch_callback = None
_active_workflow_invoke_tasks: set[asyncio.Task] = set()


def _queue_workflow_invoke_reconciliation(_snapshot, changed_key: str | None = None) -> None:
    """Queue reconciliation for durable Progenitor workflow requests."""

    task = asyncio.create_task(_reconcile_workflow_invoke_requests(changed_key))
    _active_workflow_invoke_tasks.add(task)
    task.add_done_callback(_active_workflow_invoke_tasks.discard)


_document_event_dispatcher: DocumentEventDispatcher | None = None


def get_document_event_dispatcher() -> DocumentEventDispatcher:
    """Return the backend's single document event log consumer."""

    global _document_event_dispatcher
    if _document_event_dispatcher is None:
        dispatcher = DocumentEventDispatcher(store_getter=get_default_document_store)
        dispatcher.add_handler(_publish_routine_run_lifecycle)
        dispatcher.add_handler(_dispatch_document_event_workflows)
        dispatcher.add_handler(_apply_document_event_widgets)
        dispatcher.add_handler(_record_kanban_card_activity)
        dispatcher.add_handler(_sync_kanban_card_order)
        dispatcher.add_handler(_publish_chat_session_document_event)
        _document_event_dispatcher = dispatcher
    return _document_event_dispatcher


async def _publish_routine_run_lifecycle(typed: TypedDocumentEvent) -> None:
    """Broadcast routine run lifecycle transitions logged as ``routine_run_event:`` rows."""

    await routine_events.document_event(typed.event)


async def _dispatch_document_event_workflows(typed: TypedDocumentEvent) -> None:
    """Start workflows subscribed to this event's document type."""

    executor = get_workflow_executor()
    if typed.event.key.startswith(WORKFLOW_DEFINITION_KEY_PREFIX):
        executor.invalidate_subscriptions()
        get_workflow_cron_clock().workflow_changed(typed.event.after)
    if typed.output is None:
        return
    await executor.dispatch_document_event(typed.output, origin=typed.event.origin)


async def _record_kanban_card_activity(typed: TypedDocumentEvent) -> None:
    """Derive task-activity timeline entries from kanban card updates."""

    event = typed.event
    if typed.document_type is None or typed.document_type.id != "kanban_card":
        return
    if event.op != "updated" or not isinstance(event.before, dict):
        return
    if not isinstance(event.after, dict):
        return
    _append_kanban_card_diff_activity(
        get_state_reader(),
        previous_card=event.before,
        refreshed_card=event.after,
        now=event.created_at.isoformat(),
    )


async def _sync_kanban_card_order(typed: TypedDocumentEvent) -> None:
    """Keep the board's ``card_order`` in step with the card documents.

    The board lists only cards in ``card_order``. The card routes and the
    agent's ``/tasks/`` mount maintain it themselves; this covers every other
    writer (workflow ``document_create``/``document_delete``, extensions), so a
    created card appears on the board and a deleted one leaves no stale id.
    The update carries the card event's origin, keeping run ancestry intact.
    """

    await asyncio.to_thread(_apply_kanban_card_order_event, typed.event)


def _apply_kanban_card_order_event(event: DocumentEvent) -> None:
    if not event.key.startswith(_KANBAN_CARD_KEY_PREFIX) or event.op == "updated":
        return
    card_id = event.key[len(_KANBAN_CARD_KEY_PREFIX) :]
    store = get_default_document_store()
    board = store.get_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), None)
    order = board.payload.get("card_order") if board and isinstance(board.payload, dict) else None
    listed = isinstance(order, list) and card_id in order
    if listed == (event.op == "created"):
        return

    def update(payload: Any) -> Any:
        structure = dict(payload) if isinstance(payload, dict) else {}
        current = structure.get("card_order")
        ids = [str(cid) for cid in current] if isinstance(current, list) else []
        if event.op == "created" and card_id not in ids:
            ids.append(card_id)
        elif event.op == "deleted":
            ids = [cid for cid in ids if cid != card_id]
        structure["card_order"] = ids
        return structure

    origin = document_write_origin(event.origin) if event.origin else contextlib.nullcontext()
    with origin:
        store.mutate_document(PROGENITOR_STATE_PREFIX, _kanban_board_store_key(), update)


async def _publish_chat_session_document_event(typed: TypedDocumentEvent) -> None:
    """Broadcast sidebar updates when a ``session:`` document changes."""

    event = typed.event
    if not event.key.startswith(SESSION_KEY_PREFIX):
        return
    thread_id = event.key[len(SESSION_KEY_PREFIX) :]
    # Always push the refreshed roster so every client stays in sync.
    await _broadcast_chat_sessions_snapshot()
    if event.op == "deleted":
        broadcast_ui_event(
            "chat.session.deleted",
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
        )
        return
    summary = await asyncio.to_thread(get_chat_summary, get_default_document_store(), thread_id)
    if summary is None:
        return
    broadcast_ui_event(
        "chat.session.created" if event.op == "created" else "chat.session.metadata.updated",
        {"session": summary.model_dump()},
        scope="chat",
        session_id=thread_id,
    )


_routine_agent_watch_callback = None
_active_routine_agent_tasks: set[asyncio.Task] = set()


def _queue_routine_agent_reconciliation(_snapshot, changed_key: str | None = None) -> None:
    """Always-on watcher that reconciles the ``agent``/``agent_graph`` rows
    for routine graph documents written outside the HTTP graph-update route --
    chiefly the runtime agent's ``/routines/<id>.json`` filesystem tool
    (``RoutineDirectoryBackend.awrite``). That route requires the agent row to
    already exist (see ``update_agent_graph``), so a routine the runtime
    creates from scratch would otherwise never appear on the Routines page and
    never get a canonical event -- this closes that gap so a runtime-authored
    write reaches the frontend the exact same way a UI-driven graph edit does
    (``agent.graph.updated`` / ``agents.snapshot`` on ``/api/ui/stream`` ->
    Redux, see ``app/frontend/src/store/uiData/agents.js``).

    Registered separately from ``_queue_state_watch_status``, which returns
    immediately with no UI client connected (fine for UI-push optimization,
    wrong here: the row must exist by the time someone opens the page, not
    only while they're already looking at it). ROUTINE_GRAPH_KEY_PREFIX is in
    ``state_reader._emit_if_changed``'s bypass list so no write here is
    silently dropped by the coarse snapshot diff.
    """
    if changed_key is None or changed_key.startswith("policy:"):

        async def push_policies():
            from .policy_routes import policy_catalog

            items = await asyncio.to_thread(policy_catalog, get_default_document_store())
            broadcast_ui_event("policies.snapshot", {"items": items})

        policy_task = asyncio.create_task(push_policies())
        _active_routine_agent_tasks.add(policy_task)
        policy_task.add_done_callback(_active_routine_agent_tasks.discard)
    task = asyncio.create_task(_reconcile_routine_agent(changed_key))
    _active_routine_agent_tasks.add(task)
    task.add_done_callback(_active_routine_agent_tasks.discard)


async def _reconcile_routine_agent(changed_key: str | None) -> None:
    store = get_agent_store()
    if store is None:
        return
    if changed_key is None:
        # Include rows whose documents disappeared while disconnected.
        document_ids = await asyncio.to_thread(store.list_routine_document_ids)
        catalog_ids = await asyncio.to_thread(store.list_routine_catalog_ids)
        agent_ids = sorted(set(document_ids) | set(catalog_ids))
    elif changed_key.startswith(ROUTINE_GRAPH_KEY_PREFIX):
        agent_ids = [changed_key.removeprefix(ROUTINE_GRAPH_KEY_PREFIX)]
    else:
        return

    roster_changed = False
    for agent_id in agent_ids:
        if not agent_id:
            continue
        try:
            graph_document = await asyncio.to_thread(store.get_routine_document, agent_id)
            if graph_document is None:
                deleted = await asyncio.to_thread(store.delete_routine_catalog_entry, agent_id)
                if deleted:
                    roster_changed = True
                    broadcast_ui_event("agent.deleted", {"agent_id": agent_id})
                continue
            created = await asyncio.to_thread(store.ensure_routine_agent, agent_id, graph_document)
            agent = await asyncio.to_thread(store.get_agent, agent_id)
        except Exception:
            logger.debug("Failed to reconcile routine agent %r", agent_id, exc_info=True)
            continue
        if agent is None or not isinstance(agent.get("graph"), dict):
            continue
        roster_changed = roster_changed or created
        # `agent["graph"]` goes through `_graph_payload`, which re-reads the
        # routine document from the store a second time (independently of
        # `graph_document` above). That second read has raced with the write
        # this same pass just made -- landing before it was visible -- and
        # published an empty `nodes`/`edges` graph. GraphCanvas seeds its
        # nodes/edges once per mount and never re-syncs from later prop
        # updates, so one bad broadcast during mount left the routine
        # looking permanently empty until the user forced a remount by
        # selecting a different agent and back. Source entry/nodes/edges
        # from the document we already fetched and just confirmed fresh;
        # keep status/summary from the SQL row, which only that row tracks.
        graph_payload = dict(agent["graph"])
        graph_payload["entry"] = graph_document.get("entry")
        graph_payload["nodes"] = graph_document.get("nodes", [])
        graph_payload["edges"] = graph_document.get("edges", [])
        broadcast_ui_event(
            "agent.graph.updated",
            {
                "agent_id": agent_id,
                "graph": AgentGraphModel(**graph_payload).model_dump(by_alias=True),
            },
        )
        # The `store` trigger that fired this reconcile pass also appended a
        # row to `routine_graph_revisions` (or no-opped on identical content,
        # e.g. restoring a previously captured graph). Push the
        # refreshed list so an already-open Revisions panel picks up a new
        # entry live instead of only on its next manual Refresh.
        try:
            revisions = await asyncio.to_thread(
                get_default_document_store().list_routine_revisions, agent_id
            )
        except Exception:
            logger.debug("Failed to list routine revisions for %r", agent_id, exc_info=True)
        else:
            broadcast_ui_event(
                "agent.routine.revisions.loaded",
                {
                    "agent_id": agent_id,
                    "revisions": [
                        {"revision_id": revision.revision_id, "captured_at": revision.captured_at}
                        for revision in revisions
                    ],
                },
            )

    if roster_changed:
        await _broadcast_agent_profiles_safe()


async def start_state_status_watcher(reconnect_delay: float = 2.0) -> None:
    global _state_watch_callback
    global _workflow_invoke_watch_callback, _routine_agent_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is None:
        _state_watch_callback = _queue_state_watch_status
        state_reader.add_watcher(_state_watch_callback)
    if _workflow_invoke_watch_callback is None:
        _workflow_invoke_watch_callback = _queue_workflow_invoke_reconciliation
        state_reader.add_watcher(_workflow_invoke_watch_callback)
    if _routine_agent_watch_callback is None:
        _routine_agent_watch_callback = _queue_routine_agent_reconciliation
        state_reader.add_watcher(_routine_agent_watch_callback)
    await routine_events.start()
    await get_document_event_dispatcher().start(reconnect_delay)
    await get_workflow_cron_clock().start()
    await state_reader.start_watching(reconnect_delay)


async def stop_state_status_watcher() -> None:
    global _state_watch_callback
    global _workflow_invoke_watch_callback, _routine_agent_watch_callback
    state_reader = get_state_reader()
    if _state_watch_callback is not None:
        state_reader.remove_watcher(_state_watch_callback)
        _state_watch_callback = None
    if _workflow_invoke_watch_callback is not None:
        state_reader.remove_watcher(_workflow_invoke_watch_callback)
        _workflow_invoke_watch_callback = None
    if _routine_agent_watch_callback is not None:
        state_reader.remove_watcher(_routine_agent_watch_callback)
        _routine_agent_watch_callback = None
    await routine_events.stop()
    await get_workflow_cron_clock().stop()
    await get_document_event_dispatcher().stop()
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
    global _sse_shutdown_event, _active_ui_stream_tasks

    stream_count = len(_ui_stream_subscribers)
    logger.info("Closing SSE connections: %d stream", stream_count)

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = get_sse_shutdown_event()
    shutdown_event.set()

    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}',
    }
    for queue in _ui_stream_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    tasks_to_cancel = [t for t in _active_ui_stream_tasks if not t.done()]
    if tasks_to_cancel:
        logger.info("Force-cancelling %d SSE tasks", len(tasks_to_cancel))
        for task in tasks_to_cancel:
            task.cancel()
        done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0)
        if pending:
            logger.warning("%d SSE tasks did not cancel in time", len(pending))

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
    client_id: str | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Generate the canonical typed UI event stream."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    current_task = asyncio.current_task()
    _ui_stream_subscribers[queue] = current_task
    client_id = client_id or uuid.uuid4().hex
    _ui_stream_clients[queue] = client_id
    routine_events.connections.add(client_id)
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
        await routine_events.lifecycle_snapshot(client_id)

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
                if "interest" in event and event["interest"] is not routine_events.interests.get(
                    client_id
                ):
                    continue
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
            except TimeoutError:
                envelope = _next_ui_envelope("connection.heartbeat", {"timestamp": time.time()})
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
    finally:
        _ui_stream_subscribers.pop(queue, None)
        _ui_stream_clients.pop(queue, None)
        # A reconnect may already have replaced this socket.
        if client_id not in _ui_stream_clients.values():
            routine_events.disconnected(client_id)
        if current_task:
            _active_ui_stream_tasks.discard(current_task)
        if not _ui_stream_subscribers:
            stop_status_monitor()


@router.get("/stream", response_class=EventSourceResponse)
async def ui_stream(
    response: Response,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    client_id: str | None = Query(default=None, max_length=64),
) -> AsyncGenerator[ServerSentEvent, None]:
    """Canonical typed SSE stream for all frontend UI updates."""
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_ui_stream(last_event_id, client_id):
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
    request_id: str | None = None,
    client_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast one typed event envelope to canonical /api/ui/stream subscribers.

    When *request_id* is provided it is embedded in the envelope payload so the
    frontend can correlate the event to a pending operation.
    """
    if request_id is not None:
        payload = {**payload, "request_id": request_id}
    envelope = _next_ui_envelope(event_type, payload, scope=scope, session_id=session_id)
    # Targeted detail is recovered by a subscription snapshot, never replayed
    # to other connections or to a page that has stopped watching.
    if client_id is None:
        _ui_event_replay.append(envelope)
        del _ui_event_replay[:-_UI_EVENT_REPLAY_LIMIT]

    event = {"type": "ui.event", "envelope": envelope}
    if client_id and event_type in {"agent.routine.runs.loaded", "agent.routine.run_state.loaded"}:
        event["interest"] = routine_events.interests.get(client_id)
    dead: list[asyncio.Queue] = []
    for queue in _ui_stream_subscribers:
        if client_id and _ui_stream_clients.get(queue) != client_id:
            continue
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(queue)
    for q in dead:
        task = _ui_stream_subscribers.pop(q, None)
        if task is not None and not task.done():
            # A full queue means the handler stopped draining — it is blocked
            # writing to a dead or half-open socket. Cancelling it closes the
            # HTTP connection, so a live client's EventSource reconnects
            # instead of staying open-but-deaf, and true zombies get reaped.
            task.cancel()
        logger.warning("Event queue full for UI stream subscriber — closing stale connection")
    return envelope


def broadcast_schedule_snapshot(*, request_id: str | None = None) -> UiEventEnvelope:
    """Broadcast the current schedule list to canonical /api/ui/stream subscribers.

    Emitted after backend-side schedule mutations and whenever the runtime
    reports a schedule change (fire / chat-driven CRUD) via the SSE event
    bridge, so the Schedules page reflects changes live.
    """
    state_reader = get_state_reader()
    items = [
        _schedule_item_to_info(item).model_dump(mode="json") for item in state_reader.get_schedule()
    ]
    return broadcast_ui_event("schedule.snapshot", {"items": items}, request_id=request_id)


def broadcast_kanban_snapshot(
    board: KanbanBoardResponse | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the current Tasks board to /api/ui/stream subscribers."""
    state_reader = get_state_reader()
    payload = (board or _kanban_board_response(state_reader)).model_dump(mode="json")
    return broadcast_ui_event("kanban.snapshot", payload, request_id=request_id)


def broadcast_task_content(
    card_id: str, content: TaskContentResponse | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the canonical task-body payload for one card."""
    state_reader = get_state_reader()
    if content is None:
        card = _find_kanban_card(state_reader, card_id)
        content = _read_task_content(state_reader, card)
    return broadcast_ui_event(
        "task.content.updated", content.model_dump(mode="json"), request_id=request_id
    )


def broadcast_task_activity(
    card_id: str, activity: TaskActivityResponse | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the canonical task activity payload for one card."""
    state_reader = get_state_reader()
    payload = activity or _task_activity_response(state_reader, card_id)
    return broadcast_ui_event(
        "task.activity.updated", payload.model_dump(mode="json"), request_id=request_id
    )


def broadcast_mcp_snapshot(
    items: list[WidgetResponse] | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
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
    return broadcast_ui_event("mcp.snapshot", {"items": payload}, request_id=request_id)


def broadcast_knowledge_snapshot(
    bases: list[dict[str, Any]] | None = None,
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast the current knowledge-base list to /api/ui/stream subscribers.

    Emitted after backend-side knowledge mutations and whenever the ``knowledge``
    state document changes (UI ``PUT`` or an agent edit to ``/config/knowledge.json``
    surfaced via the Postgres NOTIFY watcher), so the Knowledge page reflects
    changes live without refetching. Callers that already built the list can pass
    it in to avoid a second snapshot query and keep the event consistent with the
    HTTP response.
    """
    payload = snapshot_knowledge_bases() if bases is None else bases
    return broadcast_ui_event("knowledge.snapshot", {"bases": payload}, request_id=request_id)


def broadcast_preferences_snapshot(
    preferences: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast the current user preferences to /api/ui/stream subscribers.

    Emitted after backend-side preference mutations and whenever the
    ``preferences`` state document changes (surfaced via the Postgres NOTIFY
    watcher), so the Settings page reflects changes live without refetching.
    Callers that already built the payload can pass it in to avoid a second
    document read and keep the event consistent with the HTTP response.
    """
    payload = load_preferences() if preferences is None else preferences
    return broadcast_ui_event("preferences.snapshot", payload, request_id=request_id)


def broadcast_marketplaces_snapshot(
    marketplaces: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast the current marketplace source list to /api/ui/stream subscribers.

    Emitted after backend-side marketplace mutations and whenever the
    ``marketplaces`` state document changes (surfaced via the Postgres NOTIFY
    watcher), so the Settings page reflects changes live without refetching.
    Callers that already built the payload can pass it in to avoid a second
    document read and keep the event consistent with the HTTP response.
    """
    payload = load_marketplaces() if marketplaces is None else marketplaces
    return broadcast_ui_event("marketplaces.snapshot", payload, request_id=request_id)


def broadcast_repositories_snapshot(
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast the merged declared+scanned repository list to /api/ui/stream
    subscribers.

    Emitted after backend-side repo CRUD, whenever the ``repos`` document
    changes (agent edits via ``/config/repos.json``), and whenever the
    heartbeat's ``repo_scan`` document changes (surfaced via the Postgres
    NOTIFY watcher), so the Repositories page reflects both kinds of change
    live without refetching.
    """
    payload = {"items": merged_repo_rows(), "workspace_signal": workspace_signal()}
    return broadcast_ui_event("repositories.snapshot", payload, request_id=request_id)


def broadcast_repo_detail(repo_id: str, *, request_id: str | None = None) -> UiEventEnvelope:
    """Broadcast one repo's merged declared+scanned detail payload."""
    payload = merged_repo_detail(repo_id)
    return broadcast_ui_event("repo.detail.updated", payload, request_id=request_id)


def broadcast_plugins_snapshot(
    plugins: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast the current installed-plugins document to /api/ui/stream subscribers.

    Emitted after backend-side plugin mutations and whenever the ``plugins`` state
    document changes (surfaced via the Postgres NOTIFY watcher), so the Plugins
    page reflects changes live without refetching.
    """
    if plugins is None:
        default = default_plugins_config_payload()
        document = get_default_document_store().get_document(
            PROGENITOR_STATE_PREFIX, "plugins", default
        )
        raw = document.payload if document is not None else default
        plugins = raw if isinstance(raw, dict) else default
    return broadcast_ui_event("plugins.snapshot", plugins, request_id=request_id)


def broadcast_plugin_catalog_snapshot(
    name: str,
    catalog: dict[str, Any],
    *,
    request_id: str | None = None,
) -> UiEventEnvelope:
    """Broadcast one marketplace's fetched catalog to /api/ui/stream subscribers.

    Emitted only by the browse route; there is no store-change reaction that
    drives this event.
    """
    return broadcast_ui_event(
        "plugin.catalog.snapshot",
        {"name": name, "catalog": catalog},
        request_id=request_id,
    )


def broadcast_prompts_snapshot(
    prompts: list[PromptSummaryResponse] | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the current prompt list to /api/ui/stream subscribers.

    Emitted after the Prompts page requests the list and after UI-driven prompt
    edits, so the Prompts page (and other open tabs) reflect changes live.
    Callers that already built the list can pass it in to avoid a second
    filesystem scan and keep the event consistent with the HTTP response.
    """
    if prompts is None:
        prompts = [
            PromptSummaryResponse.model_validate(prompt, from_attributes=True)
            for prompt in PromptService().list_prompts()
        ]
    payload = [item.model_dump(mode="json") for item in prompts]
    return broadcast_ui_event("prompts.snapshot", {"prompts": payload}, request_id=request_id)


def broadcast_workflows_snapshot(
    items: list[WorkflowListItem] | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the current workflow definition list."""

    if items is None:
        items = list_workflow_list_items(get_default_document_store())
    payload = [item.model_dump(mode="json") for item in items]
    return broadcast_ui_event("workflows.snapshot", {"items": payload}, request_id=request_id)


def broadcast_workflow_detail(workflow_id: str) -> UiEventEnvelope:
    """Broadcast one workflow definition update or delete event."""

    definition = get_workflow_definition(get_default_document_store(), workflow_id)
    if definition is None:
        return broadcast_ui_event("workflow.deleted", {"workflow_id": workflow_id})
    payload = _workflow_content_response(definition).model_dump(mode="json", by_alias=True)
    return broadcast_ui_event("workflow.updated", payload)


def broadcast_workflow_runs_snapshot(
    runs: list[WorkflowRun] | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the current workflow runs list."""

    if runs is None:
        runs = list_workflow_runs(get_default_document_store())
    payload = [item.model_dump(mode="json") for item in runs]
    return broadcast_ui_event("workflow.runs.snapshot", {"items": payload}, request_id=request_id)


def broadcast_widgets_snapshot(
    items: list[WidgetResponse] | None = None, *, request_id: str | None = None
) -> UiEventEnvelope:
    """Broadcast the unified widget list (dashboard/cross-cutting views).

    Callers holding a freshly built list can pass it in to avoid recomputing
    widgets and to keep the SSE snapshot consistent with their HTTP response.
    """
    if items is None:
        state_reader = get_state_reader()
        items = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    payload = [item.model_dump(mode="json") for item in items]
    return broadcast_ui_event("widgets.updated", {"items": payload}, request_id=request_id)


async def _broadcast_chat_sessions_snapshot(include_archived: bool = True):
    return await broadcast_chat_sessions_snapshot(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
        include_archived=include_archived,
    )


def _get_active_request_id_for_thread(thread_id: str) -> str | None:
    # Deferred import: `main` imports this module, so importing `main` at
    # module load time here would be circular.
    from . import main as _main

    return _main._active_request_id_for_thread(thread_id)


router.include_router(
    create_chat_router(
        get_document_store=get_default_document_store,
        broadcast_ui_event=broadcast_ui_event,
        get_current_state=lambda thread_id: get_state_reader().get_progenitor_state(thread_id),
        get_thread_messages=lambda thread_id: get_state_reader().get_thread_messages(thread_id),
        get_active_request_id=_get_active_request_id_for_thread,
    )
)


router.include_router(
    create_agent_profile_router(
        get_agent_store=get_agent_store,
        get_document_store=get_default_document_store,
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


def broadcast_alert(alert: AlertInfo):
    """Broadcast a new alert to all subscribers."""
    broadcast_ui_event(
        "alert",
        {
            "id": alert.id,
            "type": alert.type,
            "title": alert.title,
            "message": alert.message,
            "urgent": alert.urgent,
        },
        scope="chat",
    )


def broadcast_status_change(status: str, details: dict | None = None):
    """Broadcast a status change to all subscribers."""
    broadcast_ui_event(
        "status.updated", {"status": status, "details": details or {}, "timestamp": time.time()}
    )


def broadcast_alerts_state(*, request_id: str | None = None) -> None:
    """Broadcast full alert payload updates to subscribers."""
    broadcast_ui_event("alerts.updated", _alerts_payload(), request_id=request_id)


router.include_router(create_policy_router(get_default_document_store, broadcast_ui_event))

router.include_router(
    create_model_profile_router(
        get_store=get_default_document_store,
        broadcast=broadcast_ui_event,
    )
)
