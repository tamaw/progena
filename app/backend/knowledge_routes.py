"""Knowledge base API routes — catalog, pages, and semantic search.

Base *settings* live in the ``knowledge`` state document (the single source of
truth, also editable by the agent as ``/config/knowledge.json``); pages are stored
as ``knowledge:<base>/<path>`` documents.  The backend indexer maintains the
``knowledge_chunks`` search table independently.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.progenitor.knowledge import find_base, validate_knowledge_payload
from app.progenitor.knowledge.embedding import embedder_for
from app.progenitor.knowledge.index import search as index_search
from app.progenitor.knowledge.indexer_config import INDEXER_DOC_KEY, resolve_embedding_target

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import (
    PROGENITOR_STATE_PREFIX,
    get_default_document_store,
    store_database_url,
)
from .knowledge_pages import (
    list_pages,
    orphaned_page_base_ids,
    read_page,
    rename_page,
    restore_revision,
    soft_delete_page,
)
from .knowledge_service import load_bases, save_bases, snapshot_bases
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_knowledge_snapshot, broadcast_ui_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/knowledge", tags=["knowledge"])


def _database_url() -> str:
    url = store_database_url(get_default_document_store())
    if not url:
        raise HTTPException(status_code=503, detail="Knowledge database is not configured.")
    return url


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in row.items()
    }


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def list_knowledge_bases(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a list request and publish the snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    bases = await asyncio.to_thread(snapshot_bases)
    broadcast_knowledge_snapshot(bases, request_id=request_id)
    return acknowledgement_response(request_id, ["knowledge.snapshot"])


@router.put("", status_code=202, response_model=UiRequestAcknowledgement)
async def replace_knowledge_bases(
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
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
    return acknowledgement_response(request_id, ["knowledge.snapshot"])


_REQUEST_ID_HEADER = Header(default=None, alias="X-Request-ID")
_PATH_QUERY = Query(..., description="Page path relative to the base, e.g. concepts/a.md")


async def _require_base(base_id: str) -> None:
    if not find_base(await asyncio.to_thread(load_bases), base_id):
        raise HTTPException(status_code=404, detail="Knowledge base not found.")


async def _broadcast_pages(base_id: str, request_id: str) -> None:
    pages = await asyncio.to_thread(list_pages, base_id)
    broadcast_ui_event(
        "knowledge.pages.snapshot", {"base_id": base_id, "pages": pages}, request_id=request_id
    )


def _search_rows(query: str, vector: list[float], limit: int) -> list[dict[str, Any]]:
    bases = load_bases()
    name_by_id = {str(base["id"]): base.get("name") for base in bases}
    with psycopg.connect(_database_url()) as conn:
        try:
            rows = index_search(conn, query, vector, base_ids=list(name_by_id), limit=limit)
        except psycopg.errors.UndefinedTable:
            return []
    return [{**row, "base_name": name_by_id.get(str(row["base_id"]))} for row in rows]


@router.get("/search", status_code=202, response_model=UiRequestAcknowledgement)
async def search_knowledge(
    request: Request,
    q: str = Query(min_length=1),
    limit: int = Query(default=8, ge=1, le=20),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Hybrid search across every catalog base; failures are reported in the event."""
    request_id = _request_correlation_id(request, x_request_id)
    payload: dict[str, Any] = {"query": q, "results": []}
    store = get_default_document_store()
    try:
        target = resolve_embedding_target(
            *(
                getattr(store.get_document(PROGENITOR_STATE_PREFIX, key), "payload", None)
                for key in (INDEXER_DOC_KEY, "profile")
            )
        )
        vector = (await embedder_for(target).embed([q]))[0]
        payload["results"] = [
            _serialize(row) for row in await asyncio.to_thread(_search_rows, q, vector, limit)
        ]
    except Exception as exc:  # reported to the UI instead of failing the request
        logger.info("Knowledge search failed: %s", exc)
        payload["error"] = getattr(exc, "detail", None) or str(exc)
    broadcast_ui_event("knowledge.search.results", payload, request_id=request_id)
    return acknowledgement_response(request_id, ["knowledge.search.results"])


@router.get("/{base_id}/pages", status_code=202, response_model=UiRequestAcknowledgement)
async def get_knowledge_pages(
    base_id: str, request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> UiRequestAcknowledgement:
    """Publish the live pages of one base."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    await _broadcast_pages(base_id, request_id)
    return acknowledgement_response(request_id, ["knowledge.pages.snapshot"])


@router.get("/{base_id}/page", status_code=202, response_model=UiRequestAcknowledgement)
async def get_knowledge_page(
    base_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Publish one live page (``index.md`` paths are rendered)."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    page = await asyncio.to_thread(read_page, base_id, path)
    if page is None:
        raise HTTPException(status_code=404, detail="Knowledge page not found.")
    broadcast_ui_event("knowledge.page.loaded", {"base_id": base_id, **page}, request_id=request_id)
    return acknowledgement_response(request_id, ["knowledge.page.loaded"])


@router.delete("/{base_id}/page", status_code=202, response_model=UiRequestAcknowledgement)
async def delete_knowledge_page(
    base_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Soft-delete a concept page; reserved files are refused."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    try:
        await asyncio.to_thread(soft_delete_page, base_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Knowledge page not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _broadcast_pages(base_id, request_id)
    broadcast_knowledge_snapshot(await asyncio.to_thread(snapshot_bases), request_id=request_id)
    return acknowledgement_response(request_id, ["knowledge.pages.snapshot", "knowledge.snapshot"])


@router.post("/{base_id}/page/rename", status_code=202, response_model=UiRequestAcknowledgement)
async def rename_knowledge_page(
    base_id: str,
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Move a concept page: write the new path, soft-delete the old one."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    path, new_path = str(payload.get("path") or ""), str(payload.get("new_path") or "")
    try:
        await asyncio.to_thread(rename_page, base_id, path, new_path)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Knowledge page already exists.") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Knowledge page not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _broadcast_pages(base_id, request_id)
    return acknowledgement_response(request_id, ["knowledge.pages.snapshot"])


@router.get("/{base_id}/page/revisions", status_code=202, response_model=UiRequestAcknowledgement)
async def list_knowledge_page_revisions(
    base_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Publish a page's captured revisions, newest first."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    store = get_default_document_store()
    revisions = await asyncio.to_thread(store.list_knowledge_page_revisions, base_id, path)
    broadcast_ui_event(
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
    return acknowledgement_response(request_id, ["knowledge.page.revisions.loaded"])


@router.get(
    "/{base_id}/page/revisions/{revision_id}",
    status_code=202,
    response_model=UiRequestAcknowledgement,
)
async def get_knowledge_page_revision(
    base_id: str,
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Publish one revision's content."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    store = get_default_document_store()
    content = await asyncio.to_thread(store.get_knowledge_page_revision, base_id, path, revision_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Knowledge revision not found.")
    broadcast_ui_event(
        "knowledge.page.revision.loaded",
        {"base_id": base_id, "path": path, "revision_id": revision_id, "content": content},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["knowledge.page.revision.loaded"])


@router.post(
    "/{base_id}/page/revisions/{revision_id}/restore",
    status_code=202,
    response_model=UiRequestAcknowledgement,
)
async def restore_knowledge_page_revision(
    base_id: str,
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Save a revision's content as the live page."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    if not await asyncio.to_thread(restore_revision, base_id, path, revision_id):
        raise HTTPException(status_code=404, detail="Knowledge revision not found.")
    page = await asyncio.to_thread(read_page, base_id, path)
    broadcast_ui_event(
        "knowledge.page.loaded", {"base_id": base_id, **(page or {})}, request_id=request_id
    )
    await _broadcast_pages(base_id, request_id)
    return acknowledgement_response(
        request_id, ["knowledge.page.loaded", "knowledge.pages.snapshot"]
    )
