"""A2a domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.progenitor.security.redaction import logger
from app.progenitor.utils import utcnow


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
    source: str = "a2a_send"
    request_text: str = ""
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    status: A2ATaskStatus = A2ATaskStatus.SUBMITTED
    summary: str | None = None
    latest_message: str | None = None
    last_error: str | None = None
    latest_task: dict[str, Any] | None = None
    # Compact {state, message_text, agent_name} triples, one per intermediate
    # status update seen while the task ran -- lets a reloaded chat
    # reconstruct the same progress chain a live viewer saw, instead of only
    # ever having the final snapshot (see build_task_response /
    # extract_progress_log).
    progress_log: list[dict[str, str]] = Field(default_factory=list)
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
