"""Repositories page API routes — the agent-editable ``repos`` document plus
the heartbeat-derived ``repo_scan`` read-through."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .repo_service import RepoNotFoundError, create_repo, delete_repo, update_repo
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_repo_detail, broadcast_repositories_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/repos", tags=["repos"])


class RepoCreateRequest(BaseModel):
    """Create request for a new declared repo entry."""

    name: str
    local_path: str
    access: str
    remote_url: str | None = None
    summary: str | None = None
    owner: str | None = None


class RepoUpdateRequest(BaseModel):
    """Patch request for an existing declared repo entry's mutable fields."""

    name: str | None = None
    local_path: str | None = None
    access: str | None = None
    remote_url: str | None = None
    summary: str | None = None
    owner: str | None = None


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def get_repos(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a read and publish the repositories snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    broadcast_repositories_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["repositories.snapshot"])


@router.post("", status_code=202, response_model=UiRequestAcknowledgement)
async def create_repo_entry(
    body: RepoCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Declare a new repository."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        await asyncio.to_thread(
            create_repo,
            name=body.name,
            local_path=body.local_path,
            access=body.access,
            remote_url=body.remote_url,
            summary=body.summary,
            owner=body.owner,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_repositories_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["repositories.snapshot"])


@router.patch("/{repo_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def patch_repo_entry(
    repo_id: str,
    body: RepoUpdateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Patch one declared repo entry's mutable fields."""
    request_id = _request_correlation_id(request, x_request_id)
    updates: dict[str, Any] = body.model_dump(exclude_unset=True)
    if "access" in updates and updates["access"] not in {"read", "write"}:
        raise HTTPException(status_code=400, detail="access must be 'read' or 'write'")
    try:
        await asyncio.to_thread(update_repo, repo_id, updates)
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_repositories_snapshot(request_id=request_id)
    broadcast_repo_detail(repo_id, request_id=request_id)
    return acknowledgement_response(request_id, ["repositories.snapshot", "repo.detail.updated"])


@router.delete("/{repo_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def delete_repo_entry(
    repo_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Remove one declared repo entry."""
    request_id = _request_correlation_id(request, x_request_id)
    existed = await asyncio.to_thread(delete_repo, repo_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found")
    broadcast_repositories_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["repositories.snapshot"])


@router.get("/{repo_id}/detail", status_code=202, response_model=UiRequestAcknowledgement)
async def get_repo_detail(
    repo_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a detail read and publish it on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        broadcast_repo_detail(repo_id, request_id=request_id)
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found") from exc
    return acknowledgement_response(request_id, ["repo.detail.updated"])
