"""Synchronous backend operations for stored knowledge pages."""

from __future__ import annotations

import json
from typing import Any

from app.progenitor.knowledge.indexer_config import (
    INDEX_STATUS_DOC_KEY,
    describe_index_status,
    index_record,
)
from app.progenitor.knowledge.pages import (
    INDEX_FILE,
    classify_page_path,
    render_directory_index,
    split_front_matter,
)
from app.progenitor.utils import utcnow_isoformat

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store
from .knowledge_service import load_bases


def _store() -> Any:
    return get_default_document_store()


def _key(base_id: str, path: str) -> str:
    return f"knowledge:{base_id}/{path}"


def _payload(base_id: str, path: str) -> dict[str, Any] | None:
    row = _store().get_document(PROGENITOR_STATE_PREFIX, _key(base_id, path))
    payload = row.payload if row is not None else None
    return (
        payload if isinstance(payload, dict) and isinstance(payload.get("content"), str) else None
    )


def _live(payload: dict[str, Any] | None) -> bool:
    return (
        isinstance(payload, dict)
        and not payload.get("deleted_at")
        and isinstance(payload.get("content"), str)
    )


def _base(base_id: str) -> dict[str, Any] | None:
    return next((base for base in load_bases() if str(base.get("id")) == base_id), None)


def _rows(base_id: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (key[len(f"knowledge:{base_id}/") :], document.payload)
        for key, document in _store().list_documents(
            PROGENITOR_STATE_PREFIX, f"knowledge:{base_id}/"
        )
        if isinstance(document.payload, dict)
    ]


def list_pages(base_id: str) -> list[dict[str, Any]]:
    """Live pages in a base: concepts with front matter facts, plus ``log.md``."""
    pages: list[dict[str, Any]] = []
    for path, payload in _rows(base_id):
        if not _live(payload):
            continue
        try:
            kind = classify_page_path(path)
            front_matter = (
                (split_front_matter(payload["content"])[0] or {}) if kind == "concept" else {}
            )
        except ValueError:
            continue
        pages.append(
            {
                "path": path,
                "kind": kind,
                "type": str(front_matter["type"]) if front_matter.get("type") else None,
                "title": str(front_matter.get("title") or path.rsplit("/", 1)[-1]),
                "description": str(front_matter.get("description") or ""),
                "updated_at": payload.get("updated_at"),
            }
        )
    return sorted(pages, key=lambda page: str(page["path"]))


def read_page(base_id: str, path: str) -> dict[str, Any] | None:
    base = _base(base_id)
    if base is None:
        return None
    if path == INDEX_FILE or path.endswith("/" + INDEX_FILE):
        directory = path[: -len(INDEX_FILE)].rstrip("/")
        live = [(p, payload["content"]) for p, payload in _rows(base_id) if _live(payload)]
        title = str(base.get("name") or base_id) if not directory else directory.rsplit("/", 1)[-1]
        row = _store().get_document(PROGENITOR_STATE_PREFIX, INDEX_STATUS_DOC_KEY)
        content = render_directory_index(
            title=title,
            directory=directory,
            live_pages=live,
            search_status=describe_index_status(
                index_record(row.payload if row is not None else None, base_id)
            ),
        )
        return _page_view(path, content, None)
    try:
        classify_page_path(path)
    except ValueError:
        return None
    payload = _payload(base_id, path)
    if payload is None or not _live(payload):
        return None
    return _page_view(path, payload["content"], payload.get("updated_at"))


def _page_view(path: str, content: str, updated_at: Any) -> dict[str, Any]:
    """Page payload for the UI: raw content plus parsed front matter and body."""
    try:
        front_matter, body = split_front_matter(content)
    except ValueError:
        front_matter, body = None, content
    return {
        "path": path,
        "content": content,
        # YAML dates/timestamps parse to date objects; keep the UI payload JSON-safe.
        "front_matter": json.loads(json.dumps(front_matter or {}, default=str)),
        "body": body,
        "updated_at": updated_at,
    }


def _save(base_id: str, path: str, content: str, *, deleted_at: str | None = None) -> None:
    payload: dict[str, Any] = {"content": content, "updated_at": utcnow_isoformat()}
    if deleted_at:
        payload["deleted_at"] = deleted_at
    _store().save_document(PROGENITOR_STATE_PREFIX, _key(base_id, path), payload)


def soft_delete_page(base_id: str, path: str) -> None:
    if classify_page_path(path) != "concept":
        raise ValueError("Only concept pages can be deleted")
    payload = _payload(base_id, path)
    if payload is None or not _live(payload):
        raise FileNotFoundError(path)
    _save(base_id, path, payload["content"], deleted_at=utcnow_isoformat())


def rename_page(base_id: str, path: str, new_path: str) -> None:
    if classify_page_path(path) != "concept" or classify_page_path(new_path) != "concept":
        raise ValueError("Only concept pages can be renamed")
    source = _payload(base_id, path)
    if source is None or not _live(source):
        raise FileNotFoundError(path)
    if _live(_payload(base_id, new_path)):
        raise FileExistsError(new_path)
    _save(base_id, new_path, source["content"])
    _save(base_id, path, source["content"], deleted_at=utcnow_isoformat())


def restore_revision(base_id: str, path: str, revision_id: str) -> bool:
    content = _store().get_knowledge_page_revision(base_id, path, revision_id)
    if content is None:
        return False
    _save(base_id, path, content)
    return True


def live_page_counts() -> dict[str, int]:
    return {
        str(base.get("id")): sum(
            1 for page in list_pages(str(base.get("id"))) if page["kind"] == "concept"
        )
        for base in load_bases()
    }


def orphaned_page_base_ids() -> frozenset[str]:
    """Base ids that still have stored pages but are no longer in the catalog."""
    catalog = {str(base.get("id")) for base in load_bases()}
    stored = {
        key[len("knowledge:") :].split("/", 1)[0]
        for key, _ in _store().list_documents(PROGENITOR_STATE_PREFIX, "knowledge:")
    }
    return frozenset(stored - catalog - {""})
