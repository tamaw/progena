"""State models for Progenitor using Pydantic v2."""

import logging
import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, TypedDict

from pydantic import BaseModel, Field, field_validator

from ..utils import utcnow

logger = logging.getLogger(__name__)


def generate_thread_id() -> str:
    """Generate a unique thread ID for conversation tracking."""
    return f"thread_{uuid.uuid4().hex[:12]}"


# Patterns for secret redaction - (compiled_pattern, replacement)
# Order matters: more specific patterns should come first
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-proj-[A-Za-z0-9\-_]{20,}"), "[OPENAI_PROJECT_KEY_REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[OPENAI_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(?:azure|az)[_-]?(?:openai)?[_-]?(?:api)?[_-]?key["\']?\s*[:=]\s*["\']?([A-Za-z0-9]{32,})'
        ),
        "[AZURE_KEY_REDACTED]",
    ),
    (re.compile(r"(?i)(AZURE_[A-Z_]*KEY)\s*=\s*([A-Za-z0-9]{32,})"), r"\1=[AZURE_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(?:key|secret|credential|token)["\']?\s*[:=]\s*["\']?([A-Fa-f0-9]{32})(?![A-Za-z0-9])'
        ),
        "[AZURE_KEY_REDACTED]",
    ),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "[GITHUB_TOKEN_REDACTED]"),
    (re.compile(r"gho_[A-Za-z0-9]{36,}"), "[GITHUB_OAUTH_REDACTED]"),
    (re.compile(r"ghs_[A-Za-z0-9]{36,}"), "[GITHUB_SERVER_REDACTED]"),
    (re.compile(r"ghu_[A-Za-z0-9]{36,}"), "[GITHUB_USER_REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "[GITHUB_PAT_REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "[ANTHROPIC_KEY_REDACTED]"),
    (
        re.compile(r'(?i)(api[_-]?key|apikey)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})'),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)(secret|password|passwd|pwd|credential)["\']?\s*[:=]\s*["\']?([^"\',\s]{8,})'
        ),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED]"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "[AWS_ACCESS_KEY_REDACTED]"),
    (
        re.compile(
            r'(?i)(aws[_-]?secret[_-]?access[_-]?key)["\']?\s*[:=]\s*["\']?[A-Za-z0-9/+=]{40}'
        ),
        r"\1=[REDACTED]",
    ),
]


def redact_secrets(text: str) -> str:
    """Redact sensitive information from text before logging."""
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class ScheduleEntry(BaseModel):
    """A scheduled task entry.

    Supports both recurring (cron_expression) and one-off (scheduled_at) tasks.
    Use ``paused=True`` to suspend execution without deleting the entry.
    """

    model_config = {"use_enum_values": True}

    id: str
    message: str
    cron_expression: str | None = None
    scheduled_at: str | None = None
    last_run: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    user_request: str | None = None
    paused: bool = False


class AgentStatus(StrEnum):
    """Runtime status of an A2A agent."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    EXECUTING = "executing"
    WAITING = "waiting"
    FAILED = "failed"
    STOPPING = "stopping"


class AgentRegistryEntry(BaseModel):
    """An agent or MCP server in the registry."""

    name: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    entrypoint: str = ""

    transport: str = "http"
    port: int | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    endpoint: str | None = None
    agent_card_url: str | None = None

    status: AgentStatus = AgentStatus.STOPPED
    status_message: str | None = None
    pid: int | None = None
    startup_time: datetime | None = None
    last_heartbeat: datetime | None = None
    last_error: str | None = None

    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class A2ATaskStatus(StrEnum):
    """Normalized lifecycle states for tracked A2A tasks."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"
    UNKNOWN = "unknown"


TERMINAL_A2A_TASK_STATUSES = frozenset(
    {
        A2ATaskStatus.COMPLETED.value,
        A2ATaskStatus.CANCELED.value,
        A2ATaskStatus.FAILED.value,
        A2ATaskStatus.REJECTED.value,
    }
)


class A2ATaskRecord(BaseModel):
    """Persisted metadata for an A2A task tracked by Progenitor."""

    model_config = {"use_enum_values": True}

    task_id: str
    agent_name: str
    agent_url: str | None = None
    agent_card_url: str | None = None
    transport: str = "http"
    context_id: str | None = None
    source: str = "call_agent"
    request_text: str = ""
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    status: A2ATaskStatus = A2ATaskStatus.SUBMITTED
    summary: str | None = None
    latest_message: str | None = None
    last_error: str | None = None
    latest_task: dict[str, Any] | None = None
    notify_on_completion: bool = True
    notification_sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> A2ATaskStatus:
        """Convert raw task state strings into the normalized enum."""
        if isinstance(v, A2ATaskStatus):
            return v
        if isinstance(v, str):
            try:
                return A2ATaskStatus(v)
            except ValueError:
                logger.warning("Unknown A2ATaskStatus %r, defaulting to UNKNOWN", v)
                return A2ATaskStatus.UNKNOWN
        logger.warning(
            "Unexpected A2ATaskRecord status type %s, defaulting to UNKNOWN",
            type(v).__name__,
        )
        return A2ATaskStatus.UNKNOWN

    @property
    def status_value(self) -> str:
        """Return the current status value as a string."""
        return self.status

    @property
    def is_terminal(self) -> bool:
        """Return whether the task reached a terminal lifecycle state."""
        return self.status_value in TERMINAL_A2A_TASK_STATUSES


class AsyncSubagentTaskStatus(StrEnum):
    """Lifecycle states for tracked DeepAgents async subagent tasks."""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_ASYNC_SUBAGENT_TASK_STATUSES = frozenset(
    {
        AsyncSubagentTaskStatus.SUCCESS.value,
        AsyncSubagentTaskStatus.ERROR.value,
        AsyncSubagentTaskStatus.CANCELLED.value,
    }
)


class AsyncSubagentTaskRecord(BaseModel):
    """Persisted metadata for a DeepAgents async subagent task."""

    model_config = {"use_enum_values": True}

    task_id: str
    agent_name: str
    assistant_id: str
    agent_url: str
    parent_thread_id: str | None = None
    run_id: str
    source: str = "start_async_task"
    request_text: str = ""
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    status: AsyncSubagentTaskStatus = AsyncSubagentTaskStatus.RUNNING
    summary: str | None = None
    latest_message: str | None = None
    last_error: str | None = None
    latest_run: dict[str, Any] | None = None
    notify_on_completion: bool = True
    notification_sent_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> AsyncSubagentTaskStatus:
        """Convert raw async-subagent status strings into the normalized enum."""
        if isinstance(v, AsyncSubagentTaskStatus):
            return v
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized == "cancelled":
                normalized = AsyncSubagentTaskStatus.CANCELLED.value
            try:
                return AsyncSubagentTaskStatus(normalized)
            except ValueError:
                logger.warning(
                    "Unknown AsyncSubagentTaskStatus %r, defaulting to RUNNING",
                    v,
                )
                return AsyncSubagentTaskStatus.RUNNING
        logger.warning(
            "Unexpected AsyncSubagentTaskRecord status type %s, defaulting to RUNNING",
            type(v).__name__,
        )
        return AsyncSubagentTaskStatus.RUNNING

    @property
    def status_value(self) -> str:
        """Return the current status value as a string."""
        return self.status

    @property
    def is_terminal(self) -> bool:
        """Return whether the task reached a terminal lifecycle state."""
        return self.status_value in TERMINAL_ASYNC_SUBAGENT_TASK_STATUSES


class ConversationMessage(BaseModel):
    """A message in the conversation history."""

    id: str
    role: str
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BackgroundTaskEntry(BaseModel):
    """Persisted background work item that Heartbeat can drain and execute."""

    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class ConversationState(TypedDict, total=False):
    """Conversation-related state."""

    messages: list[ConversationMessage]
    current_input: str | None
    current_input_role: str | None
    current_input_metadata: dict[str, Any] | None
    input_wait_seconds: float | None
    pending_messages_in_queue: int
    pending_response: str | None
    last_reasoning_trace: str | None
    attachments: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class SchedulingState(TypedDict, total=False):
    """Scheduling and registry state."""

    schedule: list[ScheduleEntry]
    new_schedule_entry: ScheduleEntry | None
    current_time: datetime | None
    due_items: list[str]
    scheduled_enqueued_count: int
    last_schedule_poll_at: str | None
    agents: list[AgentRegistryEntry]
    a2a_completed_task: A2ATaskRecord | None
    a2a_completed_tasks: list[A2ATaskRecord]
    last_a2a_poll_at: str | None
    a2a_poll_ran: bool
    async_subagent_completed_task: AsyncSubagentTaskRecord | None
    async_subagent_completed_tasks: list[AsyncSubagentTaskRecord]
    last_async_subagent_poll_at: str | None
    async_subagent_poll_ran: bool
    last_mcp_refresh_at: str | None
    mcp_refresh_summary: dict[str, Any] | None
    last_registry_discovery_at: str | None
    registry_discovery_summary: dict[str, Any] | None
    last_registry_refresh_at: str | None
    registry_refresh_summary: dict[str, Any] | None
    last_health_write_at: str | None
    is_scheduled_input: bool
    scheduled_message: str | None
    scheduled_result_status: str | None
    health_status_override: str | None
    health_error: str | None
    health: dict[str, Any]


class LoopControlState(TypedDict, total=False):
    """Loop control and metadata."""

    cycle_count: int
    actions_this_cycle: int
    shutdown_requested: bool
    last_commit_time: datetime | None
    thread_id: str
    requested_thread_id: str | None
    is_isolated_thread: bool
    queued_input: Any
    cycle_source: str | None
    cycle_error: str | None
    cycle_complete_signalled: bool
    idle_cycles: int
    current_backoff_seconds: float
    startup_audit_logged: bool
    heartbeat_action: str | None
    last_validation_result: dict[str, Any] | None
    validation_result_v2: dict[str, Any] | None
    last_checkpoint: str | None
    last_cycle_response: str | None


class ProgenitorState(
    ConversationState,
    SchedulingState,
    LoopControlState,
    total=False,
):
    """Main state for the active DeepAgent-backed runtime."""

    pass
