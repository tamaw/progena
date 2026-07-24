"""Backend access to the ``knowledge`` state document.

Knowledge-base settings live in one document (``progenitor.state`` / ``knowledge``)
in the shared store, so the Progenitor agent can read and edit them as the
``/config/knowledge.json`` virtual file. This module loads/saves that document and
merges per-base SQL stats (document/collection counts from ``knowledge_documents``)
into the snapshot the UI renders from.
"""

from __future__ import annotations

from typing import Any

import psycopg

from app.progenitor.knowledge import base_document_stats

from .document_store import (
    PROGENITOR_STATE_PREFIX,
    get_default_document_store,
    store_database_url,
)

KNOWLEDGE_DOC_KEY = "knowledge"
_DEFAULT_PAYLOAD: dict[str, Any] = {"bases": []}


def load_bases() -> list[dict[str, Any]]:
    """Return the knowledge-base settings list from the document store."""
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, KNOWLEDGE_DOC_KEY, _DEFAULT_PAYLOAD
    )
    payload = document.payload if document is not None else _DEFAULT_PAYLOAD
    bases = payload.get("bases") if isinstance(payload, dict) else None
    return [dict(base) for base in bases] if isinstance(bases, list) else []


def save_bases(bases: list[dict[str, Any]]) -> None:
    """Persist the knowledge-base settings list to the document store."""
    get_default_document_store().save_document(
        PROGENITOR_STATE_PREFIX, KNOWLEDGE_DOC_KEY, {"bases": bases}
    )


def snapshot_bases() -> list[dict[str, Any]]:
    """Return bases with derived SQL stats (documents/collections) merged in."""
    bases = load_bases()
    url = store_database_url(get_default_document_store())
    stats: dict[str, dict[str, Any]] = {}
    if url and bases:
        with psycopg.connect(url) as conn:
            stats = base_document_stats(conn)
    merged: list[dict[str, Any]] = []
    for base in bases:
        base_stats = stats.get(str(base.get("id")), {})
        merged.append(
            {
                **base,
                "documents": base_stats.get("documents", 0),
                "collections": base_stats.get("collections", []),
            }
        )
    return merged
