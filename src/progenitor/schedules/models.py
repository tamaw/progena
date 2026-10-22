"""Schedules domain contracts."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from src.progenitor.security.redaction import logger
from src.progenitor.utils import utcnow


def generate_schedule_run_id() -> str:
    """Generate a unique schedule run identifier."""
    return f"schedrun_{uuid.uuid4().hex[:12]}"


def generate_schedule_thread_id(schedule_id: str) -> str:
    """Generate a checkpoint thread id derived from the schedule entry id."""
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", str(schedule_id).strip()).strip("_") or "schedule"
    return f"{base}_{uuid.uuid4().hex[:8]}"


class ScheduleEntry(BaseModel):
    """A scheduled task entry.

    Supports both recurring (cron_expression) and one-off (scheduled_at) tasks.
    Use ``paused=True`` to suspend execution without deleting the entry.
    """

    # ``extra="allow"`` preserves backend/UI-authored fields (name, enabled,
    # schedule_type, timezone, next_run, ...) through runtime read/modify/write
    # cycles so a scheduled fire does not strip the document's display metadata.
    model_config = {"use_enum_values": True, "extra": "allow"}

    id: str
    message: str
    cron_expression: str | None = None
    scheduled_at: str | None = None
    last_run: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    user_request: str | None = None
    paused: bool = False


class ScheduleRunStatus(StrEnum):
    """Lifecycle states for a tracked schedule-triggered run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class ScheduleRunRecord(BaseModel):
    """Persisted metadata for a single fired schedule execution."""

    model_config = {"use_enum_values": True}

    run_id: str
    schedule_id: str
    thread_id: str
    message: str
    user_request: str | None = None
    status: ScheduleRunStatus = ScheduleRunStatus.QUEUED
    result_status: str | None = None
    response: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, v: Any) -> ScheduleRunStatus:
        """Convert raw status strings into the normalized enum."""
        if isinstance(v, ScheduleRunStatus):
            return v
        if isinstance(v, str):
            try:
                return ScheduleRunStatus(v.strip().lower())
            except ValueError:
                logger.warning("Unknown ScheduleRunStatus %r, defaulting to QUEUED", v)
                return ScheduleRunStatus.QUEUED
        logger.warning(
            "Unexpected ScheduleRunRecord status type %s, defaulting to QUEUED",
            type(v).__name__,
        )
        return ScheduleRunStatus.QUEUED
