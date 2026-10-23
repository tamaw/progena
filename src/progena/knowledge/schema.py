"""Validation schema for the knowledge state document (``/config/knowledge.json``).

The knowledge document is the catalog of knowledge bases; each base's pages live
in ``knowledge:<base-id>/<path>`` documents. The same models validate **both** the
backend ``PUT /api/ui/knowledge`` body and the agent's ``/config/knowledge.json``
virtual file, so the two write paths can never disagree on shape.

``validate_knowledge_payload`` returns a normalized dict: ids and timestamps are
filled in, tags are trimmed/deduped, and unknown/derived fields (e.g. the snapshot's
``page_count``/``index_status``) are dropped. That normalized form is what gets
persisted to the document store.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.progena.documents.catalog import check_entry_schema

from .pages import BASE_ID_RE


def slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return base or "kb"


def generate_base_id(name: str, taken: set[str]) -> str:
    candidate = slugify(name)[:48]
    if candidate not in taken:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in taken:
        suffix += 1
    return f"{candidate}-{suffix}"


def now_isoformat() -> str:
    return datetime.now(UTC).isoformat()


def _dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    clean: list[str] = []
    for tag in tags:
        label = str(tag or "").strip()
        if label and label.lower() not in seen:
            seen.add(label.lower())
            clean.append(label)
    return clean


class KnowledgeBaseModel(BaseModel):
    """One knowledge base's catalog entry."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    owner: str = Field(default="user", max_length=80)
    scope: str = Field(default="Mine", max_length=40)
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        return _dedupe_tags(value)


class KnowledgePayload(BaseModel):
    """The full ``knowledge`` document: a list of knowledge bases."""

    model_config = ConfigDict(extra="ignore")

    bases: list[KnowledgeBaseModel] = Field(default_factory=list)


def _format_validation_error(exc: ValidationError) -> str:
    """Render the first Pydantic error as a short, single-line message."""
    errors = exc.errors()
    if not errors:
        return "invalid knowledge document"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "invalid value")
    return f"{location}: {message}" if location else message


def validate_knowledge_payload(
    data: Any, *, reserved_ids: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Validate and normalize a knowledge payload."""
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    try:
        payload = KnowledgePayload.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc)) from exc

    timestamp = now_isoformat()
    provided_ids = {str(base.id).strip() for base in payload.bases if str(base.id or "").strip()}
    for base_id in provided_ids:
        if not BASE_ID_RE.fullmatch(base_id):
            raise ValueError(
                f"base id '{base_id}' must be lowercase kebab-case (a-z, 0-9, -), max 64 chars"
            )
    seen_ids: set[str] = set()
    all_taken = provided_ids | set(reserved_ids)
    bases: list[dict[str, Any]] = []
    for base in payload.bases:
        entry = base.model_dump(mode="json")
        base_id = str(entry.get("id") or "").strip()
        if not base_id:
            base_id = generate_base_id(entry["name"], taken=seen_ids | all_taken)
        if base_id in seen_ids:
            raise ValueError(f"duplicate base id '{base_id}'")
        seen_ids.add(base_id)
        entry["id"] = base_id
        entry["created_at"] = entry.get("created_at") or timestamp
        entry["updated_at"] = entry.get("updated_at") or timestamp
        check_entry_schema(f"base '{base_id}'", entry, "knowledge_base_entry")
        bases.append(entry)
    return {"bases": bases}


def validate_knowledge_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/knowledge.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_knowledge_payload(data)


def find_base(bases: list[dict[str, Any]], base_id: str) -> dict[str, Any] | None:
    """Return the base with ``base_id`` from a bases list, or None."""
    for base in bases:
        if str(base.get("id")) == base_id:
            return base
    return None


def resolve_base_ref(bases: list[dict[str, Any]], ref: str) -> str | None:
    """Resolve a base reference (exact id, else case-insensitive name) to an id."""
    needle = str(ref or "").strip()
    if not needle:
        return None
    for base in bases:
        if str(base.get("id")) == needle:
            return str(base["id"])
    lowered = needle.lower()
    for base in bases:
        if str(base.get("name", "")).strip().lower() == lowered:
            return str(base["id"])
    return None
