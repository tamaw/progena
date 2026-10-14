"""Global search route — registry-driven text search plus hand-written providers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from urllib.parse import quote

import psycopg
from fastapi import APIRouter, Header, HTTPException, Query, Request

from app.progenitor.document_kinds import (
    AGENT_DOCUMENT_KEY_PREFIX,
    DOCUMENT_KINDS,
    PROMPT_DOCUMENT_KEY_PREFIX,
    SearchSpec,
)
from app.progenitor.knowledge.embedding import embedder_for
from app.progenitor.knowledge.index import search as index_search
from app.progenitor.knowledge.indexer_config import (
    INDEXER_DOC_KEY,
    min_similarity,
    resolve_embedding_target,
)
from app.progenitor.knowledge.pages import split_front_matter

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store, store_database_url
from .knowledge_service import load_bases
from .request_utils import request_correlation_id as _request_correlation_id
from .state_reader import get_state_reader
from .ui_routes import broadcast_ui_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ui", tags=["search"])

_REQUEST_ID_HEADER = Header(default=None, alias="X-Request-ID")


# ---------------------------------------------------------------------------
# Generic text provider (runs for every kind with strategy="text")
# ---------------------------------------------------------------------------


def _build_text_result(kind, key: str, payload: Any, spec: SearchSpec) -> dict[str, Any] | None:
    """Turn one store row into a search result for the UI."""
    id_value = key.removeprefix(kind.key_prefix)

    # Agents: only object-agent SYSTEM.md documents are searchable in v1.
    if kind.key_prefix == AGENT_DOCUMENT_KEY_PREFIX:
        parts = id_value.split("/")
        if len(parts) == 3 and parts[-1] == "SYSTEM.md":
            id_value = f"object-{parts[1]}"
        else:
            return None

    to = spec.route.replace("{id}", quote(id_value, safe="/"))

    if spec.source == "front_matter":
        content = payload.get("content", "") if isinstance(payload, dict) else ""
        fm: dict[str, Any] | None = None
        if isinstance(content, str) and content.startswith("---"):
            with contextlib.suppress(Exception):
                fm, _ = split_front_matter(content)
        fm = fm or {}
        title = str(fm.get(spec.title_field, "")).strip()
        subtitle = str(fm.get("description", "") or fm.get("summary", "") or "").strip()
    else:
        if isinstance(payload, dict):
            title = str(payload.get(spec.title_field, "")).strip() if spec.title_field else ""
            subtitle = ""
            for f in spec.fields:
                if f != spec.title_field:
                    val = str(payload.get(f, "")).strip()
                    if val:
                        subtitle = val
                        break
        else:
            title = ""
            subtitle = ""

    # Prompts have no title in the payload; fall back to the filename.
    if not title and kind.key_prefix == PROMPT_DOCUMENT_KEY_PREFIX:
        title = id_value.rsplit("/", 1)[-1] if "/" in id_value else id_value

    if not title:
        title = id_value

    if len(subtitle) > 160:
        subtitle = subtitle[:157] + "…"

    return {"id": id_value, "title": title, "subtitle": subtitle, "to": to}


def _text_provider(kind, q: str, limit: int = 5) -> dict[str, Any]:
    """Search one document kind using the generic ILIKE store method."""
    spec = kind.search
    if spec is None:
        return {"query": q, "results": []}

    store = get_default_document_store()
    try:
        rows = store.search_documents(
            prefix=PROGENITOR_STATE_PREFIX,
            key_prefix=kind.key_prefix,
            fields=spec.fields,
            source=spec.source,
            q=q,
            limit=limit,
            key_suffix=spec.key_suffix,
        )
    except Exception as exc:
        logger.info("Search failed for %s: %s", kind.label, exc)
        return {
            "query": q,
            "results": [],
            "error": getattr(exc, "detail", None) or str(exc),
        }

    results: list[dict[str, Any]] = []
    for row in rows:
        result = _build_text_result(kind, row["key"], row.get("payload"), spec)
        if result is not None:
            results.append(result)

    return {"query": q, "results": results}


# ---------------------------------------------------------------------------
# Knowledge provider (hybrid vector + full-text)
# ---------------------------------------------------------------------------


def _knowledge_rows(
    query: str, vector: list[float], limit: int, min_similarity: float
) -> list[dict[str, Any]]:
    """Hybrid knowledge search across catalog bases, with each row's base name."""
    url = store_database_url(get_default_document_store())
    if not url:
        raise RuntimeError("Knowledge database is not configured.")
    name_by_id = {str(base["id"]): base.get("name") for base in load_bases()}
    with psycopg.connect(url) as conn:
        try:
            rows = index_search(
                conn,
                query,
                vector,
                min_similarity=min_similarity,
                base_ids=list(name_by_id),
                limit=limit,
            )
        except psycopg.errors.UndefinedTable:
            return []
    return [{**row, "base_name": name_by_id.get(str(row["base_id"]))} for row in rows]


async def _knowledge_provider(q: str, limit: int = 5) -> dict[str, Any]:
    store = get_default_document_store()
    try:
        settings, profiles = (
            getattr(store.get_document(PROGENITOR_STATE_PREFIX, key), "payload", None)
            for key in (INDEXER_DOC_KEY, "profile")
        )
        target = resolve_embedding_target(settings, profiles)
        vector = (await embedder_for(target).embed([q]))[0]
        rows = await asyncio.to_thread(_knowledge_rows, q, vector, limit, min_similarity(settings))
    except Exception as exc:
        logger.info("Knowledge search failed: %s", exc)
        return {
            "query": q,
            "results": [],
            "error": getattr(exc, "detail", None) or str(exc),
        }

    results = []
    for row in rows:
        base_id = str(row.get("base_id") or "")
        path = str(row.get("path") or "")
        title = row.get("title") or path
        subtitle_parts = filter(
            None,
            [row.get("heading"), row.get("base_name"), row.get("snippet")],
        )
        subtitle = " — ".join(str(p) for p in subtitle_parts)
        results.append(
            {
                "id": f"{base_id}/{path}",
                "title": title,
                "subtitle": subtitle,
                "to": f"/knowledge/{quote(base_id, safe='')}?page={quote(path, safe='')}",
            }
        )

    return {"query": q, "results": results}


# ---------------------------------------------------------------------------
# Hand-written providers (Tools, Schedules)
# ---------------------------------------------------------------------------


def _tools_provider(q: str, limit: int = 5) -> dict[str, Any]:
    state_reader = get_state_reader()
    mcp_data = state_reader.get_mcp_config()
    raw_servers = mcp_data.get("mcpServers") if isinstance(mcp_data, dict) else None
    if not isinstance(raw_servers, dict):
        return {"query": q, "results": []}

    q_lower = q.lower()
    results: list[dict[str, Any]] = []
    for name, server in raw_servers.items():
        if not isinstance(server, dict):
            continue
        haystack = f"{name} {server.get('description', '')}".lower()
        if q_lower in haystack:
            results.append(
                {
                    "id": f"mcp:{name}",
                    "title": name,
                    "subtitle": server.get("description") or "MCP server",
                    "to": f"/tools/{quote(name, safe='')}",
                }
            )

    results.sort(
        key=lambda r: (0 if r["title"].lower().startswith(q_lower) else 1, r["title"].lower())
    )
    return {"query": q, "results": results[:limit]}


def _schedules_provider(q: str, limit: int = 5) -> dict[str, Any]:
    state_reader = get_state_reader()
    items = state_reader.get_schedule()
    q_lower = q.lower()
    results: list[dict[str, Any]] = []
    for item in items:
        haystack = f"{item.name} {item.raw.get('message', '')}".lower()
        if q_lower in haystack:
            results.append(
                {
                    "id": item.id,
                    "title": item.name,
                    "subtitle": item.raw.get("message") or item.schedule or "",
                    "to": f"/schedules/{quote(str(item.id), safe='')}",
                }
            )

    results.sort(
        key=lambda r: (0 if r["title"].lower().startswith(q_lower) else 1, r["title"].lower())
    )
    return {"query": q, "results": results[:limit]}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/search", status_code=202, response_model=UiRequestAcknowledgement)
async def search(
    request: Request,
    q: str = Query(min_length=1),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Global search across documents, tools, and schedules.

    Returns 202 and broadcasts ``search.results`` with sections in fixed order.
    Each section carries its own error when the provider fails.
    """
    if len(q.strip()) < 3:
        raise HTTPException(status_code=422, detail="Query must be at least 3 characters.")

    request_id = _request_correlation_id(request, x_request_id)
    limit = 5

    # Build provider coroutines/tasks in fixed order.
    providers: list[tuple[str, asyncio.Future]] = []

    # 1. Registry-driven kinds (text strategy)
    for kind in DOCUMENT_KINDS:
        spec = kind.search
        if spec is None or spec.strategy != "text":
            continue
        providers.append(
            (spec.section, asyncio.ensure_future(asyncio.to_thread(_text_provider, kind, q, limit)))
        )

    # 2. Knowledge (hybrid vector search)
    knowledge_spec = next(
        (
            kind.search
            for kind in DOCUMENT_KINDS
            if kind.search is not None and kind.search.strategy == "knowledge"
        ),
        None,
    )
    if knowledge_spec is not None:
        providers.append(
            (knowledge_spec.section, asyncio.ensure_future(_knowledge_provider(q, limit)))
        )

    # 3. Hand-written providers
    providers.append(("Tools", asyncio.ensure_future(asyncio.to_thread(_tools_provider, q, limit))))
    providers.append(
        ("Schedules", asyncio.ensure_future(asyncio.to_thread(_schedules_provider, q, limit)))
    )

    # Run concurrently with a timeout per provider.
    sections: list[dict[str, Any]] = []
    for label, future in providers:
        try:
            payload = await asyncio.wait_for(future, timeout=2.0)
        except TimeoutError:
            payload = {"query": q, "results": [], "error": "Search timed out."}
        except Exception as exc:
            logger.info("Search provider %s failed: %s", label, exc)
            payload = {"query": q, "results": [], "error": str(exc)}

        section: dict[str, Any] = {"id": label.lower().replace(" ", "-"), "label": label}
        if payload.get("error"):
            section["error"] = payload["error"]
        if payload.get("results"):
            section["results"] = payload["results"]
        if payload.get("results") or payload.get("error"):
            sections.append(section)

    broadcast_ui_event("search.results", {"query": q, "sections": sections}, request_id=request_id)
    return acknowledgement_response(request_id, ["search.results"])
