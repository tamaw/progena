"""Backend access to the ``marketplaces`` state document.

Marketplace sources live in one document (``backend.state`` / ``marketplaces``)
in the shared store. This document is backend-owned and not agent-visible.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from .document_store import BACKEND_STATE_PREFIX, get_default_document_store

MARKETPLACES_DOC_KEY = "marketplaces"
_DEFAULT_PAYLOAD: dict[str, Any] = {"sources": []}
_CATALOG_CACHE: dict[str, tuple[float, Any]] = {}
_CATALOG_TTL_SECONDS = 300


class MarketplaceSource(BaseModel):
    name: str
    source: str
    added_at: str | None = None


class MarketplacesPayload(BaseModel):
    sources: list[MarketplaceSource]


def _format_validation_error(exc: ValidationError) -> str:
    """Render the first Pydantic error as a short, single-line message."""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "invalid value")
    return f"{location}: {message}" if location else message


def validate_marketplaces_payload(data: Any) -> dict[str, Any]:
    """Validate and normalize a marketplaces payload (``{"sources": [...]}``).

    Fills missing ``added_at`` timestamps and dedupes entries by ``name``
    (last one wins). Raises ``ValueError`` (with a concise message) on any
    schema violation.
    """
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    try:
        payload = MarketplacesPayload.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc)) from exc

    timestamp = datetime.now(UTC).isoformat()
    seen_names: set[str] = set()
    sources: list[dict[str, Any]] = []

    # Iterate in reverse so last-one-wins deduping is natural.
    for source in reversed(payload.sources):
        entry = source.model_dump(mode="json")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ValueError("every source must have a non-empty 'name'")
        source_url = str(entry.get("source") or "").strip()
        if not source_url:
            raise ValueError(f"source '{name}' is missing 'source'")
        if name in seen_names:
            continue
        seen_names.add(name)
        entry["name"] = name
        entry["source"] = source_url
        entry["added_at"] = entry.get("added_at") or timestamp
        sources.append(entry)

    return {"sources": list(reversed(sources))}


def load_marketplaces() -> dict[str, Any]:
    """Return the marketplaces document from the document store."""
    default = _DEFAULT_PAYLOAD
    document = get_default_document_store().get_document(
        BACKEND_STATE_PREFIX, MARKETPLACES_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def save_marketplaces(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist the marketplaces document and return it."""
    payload = {"sources": sources}
    get_default_document_store().save_document(BACKEND_STATE_PREFIX, MARKETPLACES_DOC_KEY, payload)
    return payload


def get_cached_catalog(name: str) -> Any | None:
    """Return a cached marketplace catalog if it has not expired."""
    entry = _CATALOG_CACHE.get(name)
    if entry is None:
        return None
    fetched_at, catalog = entry
    if time.monotonic() - fetched_at > _CATALOG_TTL_SECONDS:
        _CATALOG_CACHE.pop(name, None)
        return None
    return catalog


def set_cached_catalog(name: str, catalog: Any) -> None:
    """Cache a marketplace catalog in-process."""
    _CATALOG_CACHE[name] = (time.monotonic(), catalog)
