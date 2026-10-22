"""Repositories page API routes — the agent-editable ``repo:<id>`` documents
plus the heartbeat-derived ``repo_scan`` read-through (and on-demand write, via
``/fetch``, for the "Fetch" action)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

import app.backend.events.contracts as events_contracts
import app.backend.events.stream as events_stream
import app.backend.http.contracts as http_contracts
import app.backend.repositories.service as repositories_service
from app.backend.http.requests import request_correlation_id as _request_correlation_id
from app.backend.repositories.service import (
    RepoFetchError,
    RepoNotFoundError,
    create_repo,
    delete_repo,
    fetch_and_rescan_repo,
    update_repo,
)

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


@router.get("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_repos(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Acknowledge a read and publish the repositories snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    broadcast_repositories_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["repositories.snapshot"])


@router.post("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def create_repo_entry(
    body: RepoCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
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
    return http_contracts.acknowledgement_response(request_id, ["repositories.snapshot"])


@router.patch("/{repo_id}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def patch_repo_entry(
    repo_id: str,
    body: RepoUpdateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
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
    return http_contracts.acknowledgement_response(
        request_id, ["repositories.snapshot", "repo.detail.updated"]
    )


@router.delete(
    "/{repo_id}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def delete_repo_entry(
    repo_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Remove one declared repo entry."""
    request_id = _request_correlation_id(request, x_request_id)
    existed = await asyncio.to_thread(delete_repo, repo_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found")
    broadcast_repositories_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["repositories.snapshot"])


@router.get(
    "/{repo_id}/detail", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_repo_detail(
    repo_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Acknowledge a detail read and publish it on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        broadcast_repo_detail(repo_id, request_id=request_id)
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found") from exc
    return http_contracts.acknowledgement_response(request_id, ["repo.detail.updated"])


@router.post(
    "/{repo_id}/fetch", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def fetch_repo_entry(
    repo_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Run ``git fetch`` against one declared repo and broadcast the refreshed scan.

    On-demand only -- checks whether the repo is stale relative to its
    upstream without merging anything. Distinct from the heartbeat's
    periodic local-only scan (see ``repo_service.fetch_and_rescan_repo``).
    """
    request_id = _request_correlation_id(request, x_request_id)
    try:
        await fetch_and_rescan_repo(repo_id)
    except RepoNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"repo '{repo_id}' not found") from exc
    except RepoFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    broadcast_repositories_snapshot(request_id=request_id)
    broadcast_repo_detail(repo_id, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, ["repositories.snapshot", "repo.detail.updated"]
    )


def broadcast_repositories_snapshot(
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the merged declared+scanned repository list to /api/ui/stream
    subscribers.

    Emitted after backend-side repo CRUD, whenever a ``repo:<id>`` document
    changes (agent edits via ``/config/repos/<id>.json``), and whenever the
    heartbeat's ``repo_scan`` document changes (surfaced via the Postgres
    NOTIFY watcher), so the Repositories page reflects both kinds of change
    live without refetching.
    """
    payload = {
        "items": repositories_service.merged_repo_rows(),
        "workspace_signal": repositories_service.workspace_signal(),
    }
    return events_stream.broadcast_ui_event("repositories.snapshot", payload, request_id=request_id)


def broadcast_repo_detail(
    repo_id: str, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast one repo's merged declared+scanned detail payload."""
    payload = repositories_service.merged_repo_detail(repo_id)
    return events_stream.broadcast_ui_event("repo.detail.updated", payload, request_id=request_id)
