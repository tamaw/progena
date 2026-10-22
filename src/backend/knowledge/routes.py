"""Knowledge base API routes — catalog, pages, and revisions.

Base *settings* live in the ``knowledge`` state document (the single source of
truth, also editable by the agent as ``/config/knowledge.json``); pages are stored
as ``knowledge:<base>/<path>`` documents. Knowledge search is a section of the
global search (``search_routes.py``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.knowledge.service as knowledge_service
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.knowledge.pages import list_pages, orphaned_page_base_ids, read_page
from src.backend.knowledge.service import load_bases, save_bases, snapshot_bases
from src.backend.storage.documents import get_default_document_store
from src.progenitor.documents.front_matter import split_front_matter
from src.progenitor.knowledge.schema import find_base, validate_knowledge_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/knowledge", tags=["knowledge"])


@router.get("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def list_knowledge_bases(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Acknowledge a list request and publish the snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    bases = await asyncio.to_thread(snapshot_bases)
    broadcast_knowledge_snapshot(bases, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["knowledge.snapshot"])


@router.put("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def replace_knowledge_bases(
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Validate and persist the full knowledge-base settings document.

    The whole ``{"bases": [...]}`` payload is validated, saved, and the snapshot
    is broadcast so every open Knowledge page updates live.  The backend indexer
    cleans up orphaned chunks independently.
    """
    request_id = _request_correlation_id(request, x_request_id)
    try:
        reserved = await asyncio.to_thread(orphaned_page_base_ids)
        normalized = validate_knowledge_payload(payload, reserved_ids=reserved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_bases = normalized["bases"]
    await asyncio.to_thread(save_bases, new_bases)
    bases = await asyncio.to_thread(snapshot_bases)
    broadcast_knowledge_snapshot(bases, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["knowledge.snapshot"])


_REQUEST_ID_HEADER = Header(default=None, alias="X-Request-ID")
_PATH_QUERY = Query(..., description="Page path relative to the base, e.g. concepts/a.md")


async def _require_base(base_id: str) -> None:
    if not find_base(await asyncio.to_thread(load_bases), base_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found.")


async def _broadcast_pages(base_id: str, request_id: str) -> None:
    pages = await asyncio.to_thread(list_pages, base_id)
    events_stream.broadcast_ui_event(
        "knowledge.pages.snapshot", {"base_id": base_id, "pages": pages}, request_id=request_id
    )


@router.get(
    "/{base_id}/pages", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_knowledge_pages(
    base_id: str, request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    """Publish the live pages of one base."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    await _broadcast_pages(base_id, request_id)
    return http_contracts.acknowledgement_response(request_id, ["knowledge.pages.snapshot"])


@router.get(
    "/{base_id}/page", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_knowledge_page(
    base_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Publish one live page (``index.md`` paths are rendered)."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    page = await asyncio.to_thread(read_page, base_id, path)
    if page is None:
        raise HTTPException(status_code=404, detail="Knowledge page not found.")
    events_stream.broadcast_ui_event(
        "knowledge.page.loaded", {"base_id": base_id, **page}, request_id=request_id
    )
    return http_contracts.acknowledgement_response(request_id, ["knowledge.page.loaded"])


@router.get(
    "/{base_id}/page/revisions",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def list_knowledge_page_revisions(
    base_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Publish a page's captured revisions, newest first."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    store = get_default_document_store()
    revisions = await asyncio.to_thread(store.list_knowledge_page_revisions, base_id, path)
    events_stream.broadcast_ui_event(
        "knowledge.page.revisions.loaded",
        {
            "base_id": base_id,
            "path": path,
            "revisions": [
                {"revision_id": r.revision_id, "captured_at": r.captured_at} for r in revisions
            ],
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["knowledge.page.revisions.loaded"])


@router.get(
    "/{base_id}/page/revisions/{revision_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_knowledge_page_revision(
    base_id: str,
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Publish one revision's content."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    store = get_default_document_store()
    content = await asyncio.to_thread(store.get_knowledge_page_revision, base_id, path, revision_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Knowledge revision not found.")
    try:
        front_matter, _ = split_front_matter(content)
    except ValueError:
        front_matter = None
    events_stream.broadcast_ui_event(
        "knowledge.page.revision.loaded",
        {
            "base_id": base_id,
            "path": path,
            "revision_id": revision_id,
            "content": content,
            "front_matter": front_matter,
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["knowledge.page.revision.loaded"])


def broadcast_knowledge_snapshot(
    bases: list[dict[str, Any]] | None = None,
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current knowledge-base list to /api/ui/stream subscribers.

    Emitted after backend-side knowledge mutations and whenever the ``knowledge``
    state document changes (UI ``PUT`` or an agent edit to ``/config/knowledge.json``
    surfaced via the Postgres NOTIFY watcher), so the Knowledge page reflects
    changes live without refetching. Callers that already built the list can pass
    it in to avoid a second snapshot query and keep the event consistent with the
    HTTP response.
    """
    payload = knowledge_service.snapshot_bases() if bases is None else bases
    return events_stream.broadcast_ui_event(
        "knowledge.snapshot", {"bases": payload}, request_id=request_id
    )


def broadcast_knowledge_pages_snapshot(
    base_id: str, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    from src.backend.knowledge.pages import list_pages

    return events_stream.broadcast_ui_event(
        "knowledge.pages.snapshot",
        {"base_id": base_id, "pages": list_pages(base_id)},
        request_id=request_id,
    )
