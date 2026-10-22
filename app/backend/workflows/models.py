"""Workflows models."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.progenitor.workflows import (
    WORKFLOW_DEFAULT_DOCUMENT_TYPE,
    WorkflowDefinition,
    WorkflowListItem,
    WorkflowRun,
)

logger = logging.getLogger(__name__)


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
