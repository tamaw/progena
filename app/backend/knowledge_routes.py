"""Knowledge base API routes — pgvector stores for the Knowledge tab.

Base *settings* live in the ``knowledge`` state document (the single source of
truth, also editable by the agent as ``/state/knowledge.json``); the documents and
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
        normalized = validate_knowledge_payload(payload)
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
