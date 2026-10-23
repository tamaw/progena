"""Async_subagents domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.progena.security.redaction import logger
from src.progena.utils import utcnow


class AsyncSubagentTaskStatus(StrEnum):
    """Lifecycle states for tracked DeepAgents async subagent tasks."""

    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


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
