"""Backend access to the ``marketplaces`` state document.

Registered marketplace sources live in the ``marketplaces`` document under the
``progenitor.state`` namespace -- the same shared Postgres ``store`` table
Progenitor's own runtime reads and writes, and the same document the agent can
edit directly at ``/config/marketplaces.json`` (see
``app/progenitor/agent/state_backend.py``). The backend reads/writes it with
straight SQL through ``PostgresDocumentStore``, no RPC to the runtime process
needed; the existing LISTEN/NOTIFY trigger on the ``store`` table (scoped to
``progenitor.state``/``backend.state`` prefixes) pushes the change back to the
UI stream the same way it already does for ``plugins``/``mcp``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.progenitor.agent.state_backend import validate_marketplaces_document
from app.progenitor.config import default_marketplaces_config_payload

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store

MARKETPLACES_DOC_KEY = "marketplaces"
_CATALOG_CACHE: dict[str, tuple[float, Any]] = {}
_CATALOG_TTL_SECONDS = 300


def validate_marketplaces_payload(data: Any) -> dict[str, Any]:
    """Validate a marketplaces payload (``{"marketplaces": {"<name>": {...}}}``).

    Thin wrapper around the same validator the agent's ``/config/marketplaces.json``
    writes go through, so both paths enforce one schema. Raises ``ValueError``
    (with a concise message) on any schema violation.
    """
    validated = validate_marketplaces_document(json.dumps(data))
    if not isinstance(validated, dict):
        raise ValueError("top level must be a JSON object")
    return validated


def load_marketplaces() -> dict[str, Any]:
    """Return the marketplaces document from the document store."""
    default = default_marketplaces_config_payload()
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, MARKETPLACES_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def save_marketplaces(marketplaces: dict[str, Any]) -> dict[str, Any]:
    """Persist the marketplaces document and return it."""
    payload = {"marketplaces": marketplaces}
    get_default_document_store().save_document(
        PROGENITOR_STATE_PREFIX, MARKETPLACES_DOC_KEY, payload
    )
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
