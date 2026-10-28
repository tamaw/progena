"""Schedules models."""

from __future__ import annotations

import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ScheduleRunInfo(BaseModel):
    """Read-only presentation of a schedule execution."""

    run_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    thread_id: str
    error: str | None = None


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
    runs: list[ScheduleRunInfo] = []


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


class WidgetFieldOptionResponse(BaseModel):
    """Selectable option for a kanban card field."""

    value: str
    label: str


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
