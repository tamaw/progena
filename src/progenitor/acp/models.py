"""Acp domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from src.progenitor.security.redaction import logger
from src.progenitor.utils import utcnow


class ACPAgentConfig(BaseModel):
    """Invocation config for an Agent Client Protocol (ACP) agent.

    Carried on an ``AgentRegistryEntry`` with ``transport="acp"``. Agents may
    be local stdio subprocesses or long-lived TCP ACP servers. ``profile``
    names a known stdio agent whose flag builder lives in
    ``src/progenitor/acp/agents.py``; ``args`` (when set) overrides the profile
    builder with raw arguments.

    ``host`` and ``port`` select a TCP server. ``model`` is selected per TCP
    session when the agent supports it, and ``cwd`` is passed to every ACP
    new/resume session unless the task supplies its own working directory.
    """

    command: str = "copilot"
    profile: str | None = "copilot"
    args: list[str] = Field(default_factory=list)
    model: str | None = None
    flags: list[str] = Field(default_factory=list)
    timeout_seconds: int = 1200
    host: str | None = None
    port: int | None = None
    cwd: str | None = None


class ACPTaskStatus(StrEnum):
    """Lifecycle states for one persisted ACP task turn sequence."""

    QUEUED = "queued"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


TERMINAL_ACP_TASK_STATUSES = frozenset(
    {
        ACPTaskStatus.COMPLETED.value,
        ACPTaskStatus.FAILED.value,
        ACPTaskStatus.CANCELED.value,
        ACPTaskStatus.INTERRUPTED.value,
    }
)


ACPFailureKind = Literal["usage_limit", "error"]


class ACPTaskRecord(BaseModel):
    """Persisted request and supervisor state for one ACP session.

    Request-owned fields are deliberately separate from the fields written by
    the backend supervisor.  A request may queue a next prompt while the prior
    turn is still working, without losing either the new prompt or the prior
    turn's recorded history.
    """

    model_config = {"use_enum_values": True}

    task_id: str
    agent_name: str
    prompt: str = ""
    cwd: str | None = None
    card_id: str | None = None
    status: ACPTaskStatus = ACPTaskStatus.QUEUED
    cancel_requested: bool = False
    messages: list[dict[str, Any]] = Field(default_factory=list)
    mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] | None = None
    answers: dict[str, Any] = Field(default_factory=dict)
    source: str = "workflow"
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    notify_on_completion: bool = True
    command: str = ""
    session_id: str | None = None
    turn: int = 0
    output: str | None = None
    summary: str | None = None
    stop_reason: str | None = None
    last_error: str | None = None
    # Set by the supervisor when a turn ends `failed`: "usage_limit" when the
    # agent's account is out of capacity, else "error"; null otherwise.
    failure_kind: ACPFailureKind | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    progress: dict[str, Any] = Field(
        default_factory=lambda: {
            "last_message": None,
            "plan": [],
            "tool_call_count": 0,
            "last_tool": None,
            "last_activity_at": None,
        }
    )
    stalled: bool = False
    history: list[dict[str, Any]] = Field(default_factory=list)
    pending_input: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notification_sent_at: datetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> ACPTaskStatus:
        """Convert raw status strings into the normalized enum."""
        if isinstance(v, ACPTaskStatus):
            return v
        if isinstance(v, str):
            try:
                return ACPTaskStatus(v.strip().lower())
            except ValueError:
                logger.warning("Unknown ACPTaskStatus %r, defaulting to QUEUED", v)
                return ACPTaskStatus.QUEUED
        logger.warning(
            "Unexpected ACPTaskRecord status type %s, defaulting to QUEUED",
            type(v).__name__,
        )
        return ACPTaskStatus.QUEUED

    @property
    def status_value(self) -> str:
        """Return the current status value as a string."""
        return self.status

    @property
    def is_terminal(self) -> bool:
        """Return whether the run reached a terminal lifecycle state."""
        return self.status_value in TERMINAL_ACP_TASK_STATUSES
