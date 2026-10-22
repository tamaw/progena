"""Search-indexer settings (``/config/indexer.json``) and status document keys.

The settings document names which model profile (``/config/profile.json``) embeds
knowledge pages and at what dimension. The backend indexer writes its progress to
a separate ``knowledge_index`` document (``/knowledge/indexer.json``), so agent
edits to the settings and indexer status writes never overwrite each other.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.progenitor.documents.catalog import check_entry_schema

INDEXER_DOC_KEY = "indexer"
INDEX_STATUS_DOC_KEY = "knowledge_index"
DEFAULT_DIMENSIONS = 1536
# Calibrated on text-embedding-3-small: on-topic chunks score ~0.3-0.5, unrelated ones ~0.1-0.2.
DEFAULT_MIN_SIMILARITY = 0.25

NOT_CONFIGURED_MESSAGE = (
    'Set "profile" in /config/indexer.json to an embeddings deployment from /config/profile.json'
)


@dataclass(frozen=True)
class EmbeddingTarget:
    """The resolved embedding model and Foundry provider for the index."""

    profile: str
    deployment: str
    dimensions: int
    provider_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "provider_id": self.provider_id,
            "deployment": self.deployment,
            "dimensions": self.dimensions,
        }


def default_indexer_payload() -> dict[str, Any]:
    return {
        "profile": "",
        "dimensions": DEFAULT_DIMENSIONS,
        "min_similarity": DEFAULT_MIN_SIMILARITY,
    }


def validate_indexer_payload(
    payload: Any, *, profile_names: Iterable[str] | None = None
) -> dict[str, Any]:
    """Validate the settings document; with ``profile_names``, the profile must exist."""
    if not isinstance(payload, dict):
        raise ValueError("indexer.json must be a JSON object")
    check_entry_schema("indexer.json", payload, "indexer")
    normalized = {**default_indexer_payload(), **payload}
    normalized["profile"] = str(normalized["profile"]).strip()
    if profile_names is not None and normalized["profile"]:
        names = set(profile_names)
        if normalized["profile"] not in names:
            known = ", ".join(sorted(names)) or "none"
            raise ValueError(
                f"profile {normalized['profile']!r} is not in /config/profile.json (known: {known})"
            )
    return normalized


def validate_indexer_document(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"indexer.json must be valid JSON: {exc}") from exc
    return validate_indexer_payload(payload)


def resolve_embedding_target(
    settings: Mapping[str, Any] | None, profiles: Mapping[str, Any] | None
) -> EmbeddingTarget:
    """Resolve settings + model profiles to the deployment to embed with.

    Raises ``ValueError`` with an agent-readable fix when unset or unknown.
    """
    data = {**default_indexer_payload(), **dict(settings or {})}
    name = str(data.get("profile") or "").strip()
    profile = (profiles or {}).get(name) if name else None
    deployment = str(profile.get("deployment") or "").strip() if isinstance(profile, dict) else ""
    provider_id = str(profile.get("provider_id") or "").strip() if isinstance(profile, dict) else ""
    if not deployment or not provider_id:
        raise ValueError(NOT_CONFIGURED_MESSAGE)
    return EmbeddingTarget(
        name,
        deployment,
        int(data.get("dimensions") or DEFAULT_DIMENSIONS),
        provider_id,
    )


def min_similarity(settings: Mapping[str, Any] | None) -> float:
    """Cosine-similarity cut-off for search results that don't also match by keyword."""
    value = (settings or {}).get("min_similarity", DEFAULT_MIN_SIMILARITY)
    return float(value) if isinstance(value, int | float) else DEFAULT_MIN_SIMILARITY


def index_record(status_doc: Mapping[str, Any] | None, base_id: str) -> dict[str, Any] | None:
    """The status record for ``base_id`` from the ``knowledge_index`` document."""
    bases = (status_doc or {}).get("bases")
    record = bases.get(base_id) if isinstance(bases, dict) else None
    return record if isinstance(record, dict) else None


def index_front_matter(record: Mapping[str, Any] | None) -> dict[str, Any]:
    """A base's search-index state for the ``search_index`` front matter of its
    generated root ``index.md``: status, page counts, and when it was indexed or why
    it failed."""
    if not record:
        return {"status": "not_indexed"}
    data: dict[str, Any] = {
        "status": str(record.get("status") or "not_indexed"),
        "pages": int(record.get("pages") or 0),
        "indexed_pages": int(record.get("indexed_pages") or 0),
    }
    if record.get("indexed_at"):
        data["indexed_at"] = str(record["indexed_at"])
    if record.get("error"):
        data["error"] = str(record["error"])
    return data


def describe_index_status(record: Mapping[str, Any] | None) -> str:
    """One-line search-index status for the root ``/knowledge/index.md`` list, e.g. ``indexing 3/7``."""
    if not record:
        return "not indexed yet"
    status = str(record.get("status") or "")
    pages = int(record.get("pages") or 0)
    if status == "indexed":
        at = str(record.get("indexed_at") or "")[:16].replace("T", " ")
        return f"indexed {pages} page{'s' if pages != 1 else ''}" + (f" ({at} UTC)" if at else "")
    if status == "indexing":
        return f"indexing {int(record.get('indexed_pages') or 0)}/{pages}"
    if status == "failed":
        return f"failed: {record.get('error') or 'unknown error'}"
    return "not indexed yet"
