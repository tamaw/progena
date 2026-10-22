"""Tasks models."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

import app.backend.schedules.models as schedules_models

logger = logging.getLogger(__name__)


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
    options: list[schedules_models.WidgetFieldOptionResponse] = Field(default_factory=list)


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
    repo_id: str | None = None
    acp_task_id: str | None = None
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
    repo_id: str | None = None
    acp_task_id: str | None = None
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
