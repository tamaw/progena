"""Backend access to the ``knowledge`` state document.

Knowledge-base settings live in one document (``progenitor.state`` / ``knowledge``)
in the shared store, so the Progenitor agent can read and edit them as the
``/config/knowledge.json`` virtual file.  This module loads/saves that document and
merges per-base index status and page counts into the snapshot the UI renders.
"""

from __future__ import annotations

from typing import Any

from src.backend.storage.documents import PROGENITOR_STATE_PREFIX, get_default_document_store
from src.progenitor.knowledge.indexer_config import INDEX_STATUS_DOC_KEY, index_record

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
    """Return bases with derived index status and page stats merged in."""
    bases = load_bases()
    from src.backend.knowledge.pages import live_page_counts

    page_counts = live_page_counts()
    store = get_default_document_store()
    status_doc = store.get_document(PROGENITOR_STATE_PREFIX, INDEX_STATUS_DOC_KEY)
    status_payload = status_doc.payload if status_doc is not None else None
    merged: list[dict[str, Any]] = []
    for base in bases:
        base_id = str(base.get("id"))
        record = index_record(status_payload, base_id) or {}
        merged.append(
            {
                **base,
                "path": f"/knowledge/{base_id}/",
                "page_count": page_counts.get(base_id, 0),
                "index_status": record.get("status") or "indexing",
                "index_pages": record.get("pages") or 0,
                "indexed_pages": record.get("indexed_pages") or 0,
                "index_error": record.get("error") or "",
                "indexed_at": record.get("indexed_at") or None,
            }
        )
    return merged
