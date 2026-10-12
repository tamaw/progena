"""Knowledge base API routes — pgvector stores for the Knowledge tab.

Base *settings* live in the ``knowledge`` state document (the single source of
truth, also editable by the agent as ``/config/knowledge.json``); the documents and
their vectors live in the ``knowledge_documents`` SQL table. The page renders from
the realtime ``knowledge.snapshot`` event, so this router exposes one validated
settings write (``PUT``), the folder-index action (the only thing that can't be a
JSON edit), and vector search.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.progenitor.knowledge import (
    delete_documents_for_base,
    delete_documents_for_source,
    find_base,
    index_folder,
    now_isoformat,
    resolve_base_ref,
    search,
    validate_knowledge_payload,
)

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import get_default_document_store, store_database_url
from .knowledge_pages import (
    list_pages,
    orphaned_page_base_ids,
    read_page,
    rename_page,
    restore_revision,
    save_instructions,
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

    The whole ``{"bases": [...]}`` payload is validated, SQL documents for any
    removed base or source are cleaned up, the document is saved, and the snapshot
    is broadcast so every open Knowledge page updates live.
    """
    request_id = _request_correlation_id(request, x_request_id)
    try:
        reserved = await asyncio.to_thread(orphaned_page_base_ids)
        normalized = validate_knowledge_payload(payload, reserved_ids=reserved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    new_bases = normalized["bases"]

    def _work() -> list[dict[str, Any]]:
        current = load_bases()
        new_base_ids = {base["id"] for base in new_bases}
        new_source_ids = {source["id"] for base in new_bases for source in base.get("sources", [])}
        removed_base_ids = {base["id"] for base in current} - new_base_ids
        removed_source_ids = {
            source["id"] for base in current for source in base.get("sources", [])
        } - new_source_ids
        with psycopg.connect(_database_url()) as conn:
            for base_id in removed_base_ids:
                delete_documents_for_base(conn, base_id)
            for source_id in removed_source_ids:
                delete_documents_for_source(conn, source_id)
        save_bases(new_bases)
        return snapshot_bases()

    bases = await asyncio.to_thread(_work)
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


@router.put("/{base_id}/instructions", status_code=202, response_model=UiRequestAcknowledgement)
async def put_knowledge_instructions(
    base_id: str,
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Save the base's ``INSTRUCTIONS.md`` brief."""
    request_id = _request_correlation_id(request, x_request_id)
    await _require_base(base_id)
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content must be a string")
    await asyncio.to_thread(save_instructions, base_id, content)
    await _broadcast_pages(base_id, request_id)
    return acknowledgement_response(request_id, ["knowledge.pages.snapshot"])


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


@router.post(
    "/{base_id}/sources/{source_id}/index", status_code=202, response_model=UiRequestAcknowledgement
)
async def index_knowledge_source(
    base_id: str,
    source_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Walk + embed a folder source into SQL and record its status on the doc."""
    request_id = _request_correlation_id(request, x_request_id)

    def _work() -> tuple[list[dict[str, Any]] | None, str | None]:
        bases = load_bases()
        base = find_base(bases, base_id)
        if base is None:
            return None, "base"
        source = next(
            (item for item in base.get("sources", []) if item.get("id") == source_id), None
        )
        if source is None:
            return None, "source"
        with psycopg.connect(_database_url()) as conn:
            result = index_folder(
                conn, base_id=base_id, source_id=source_id, path=str(source.get("path") or "")
            )
        source.update(result)
        base["updated_at"] = now_isoformat()
        save_bases(bases)
        return snapshot_bases(), None

    bases, error = await asyncio.to_thread(_work)
    if error == "base":
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    if error == "source":
        raise HTTPException(status_code=404, detail="Source not found.")
    assert bases is not None
    broadcast_knowledge_snapshot(bases, request_id=request_id)
    return acknowledgement_response(request_id, ["knowledge.snapshot"])


@router.get("/{base_id}/search", status_code=202, response_model=UiRequestAcknowledgement)
async def search_knowledge_base(
    request: Request,
    base_id: str,
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    request_id = _request_correlation_id(request, x_request_id)

    def _work() -> tuple[str, list[dict[str, Any]]] | None:
        bases = load_bases()
        resolved = resolve_base_ref(bases, base_id)
        if resolved is None:
            return None
        with psycopg.connect(_database_url()) as conn:
            rows = search(conn, q, base_id=resolved, limit=limit)
        name_by_id = {str(base["id"]): base.get("name") for base in bases}
        for row in rows:
            row["base_name"] = name_by_id.get(str(row.get("base_id")))
        return resolved, rows

    search_result = await asyncio.to_thread(_work)
    if search_result is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    resolved_base_id, rows = search_result
    broadcast_ui_event(
        "knowledge.search.results",
        {
            "base_id": resolved_base_id,
            "query": q,
            "limit": limit,
            "count": len(rows),
            "results": [_serialize(row) for row in rows],
        },
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["knowledge.search.results"])
