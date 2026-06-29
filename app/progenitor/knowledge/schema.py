"""Validation schema for the knowledge state document (``/state/knowledge.json``).

The knowledge document is the single source of truth for knowledge-base *settings*
(the documents + their vectors live in the ``knowledge_documents`` SQL table). The
same models validate **both** the backend ``PUT /api/ui/knowledge`` body and the
agent's ``/state/knowledge.json`` virtual file, so the two write paths can never
disagree on shape.

``validate_knowledge_payload`` returns a normalized dict: ids and timestamps are
filled in, tags are trimmed/deduped, and unknown/derived fields (e.g. the snapshot's
``documents``/``collections`` counts) are dropped. That normalized form is what gets
persisted to the document store.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .embedding import EMBED_DIM, EMBED_MODEL_NAME

VALID_METRICS = ("cosine", "l2", "inner_product")
VALID_INDEX_TYPES = ("Flat", "HNSW", "IVFFlat")
VALID_STATUSES = ("connected", "syncing", "error", "idle")
VALID_SOURCE_STATUSES = ("pending", "indexed", "error")

_INDEX_TYPE_CANON = {value.lower(): value for value in VALID_INDEX_TYPES}


def slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return base or "kb"


def generate_base_id(name: str) -> str:
    return f"{slugify(name)[:32]}-{uuid.uuid4().hex[:6]}"


def generate_source_id() -> str:
    return f"src-{uuid.uuid4().hex[:10]}"


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


class KnowledgeSourceModel(BaseModel):
    """One indexable folder source attached to a base."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=80)
    path: str = Field(min_length=1, max_length=1000)
    status: str = Field(default="pending")
    document_count: int = Field(default=0, ge=0)
    error: str = Field(default="", max_length=2000)
    last_indexed_at: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = str(value or "pending").strip().lower()
        if normalized not in VALID_SOURCE_STATUSES:
            raise ValueError(f"source status must be one of {VALID_SOURCE_STATUSES}")
        return normalized


class KnowledgeBaseModel(BaseModel):
    """One knowledge base's settings."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default="", max_length=120)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    owner: str = Field(default="user", max_length=80)
    scope: str = Field(default="Mine", max_length=40)
    status: str = Field(default="connected")
    embedding_model: str = Field(default=EMBED_MODEL_NAME, max_length=120)
    dimensions: int = Field(default=EMBED_DIM, ge=1, le=4096)
    metric: str = Field(default="cosine", max_length=40)
    index_type: str = Field(default="Flat", max_length=40)
    tags: list[str] = Field(default_factory=list)
    connection_string: str = Field(default="", max_length=500)
    sources: list[KnowledgeSourceModel] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        normalized = str(value or "connected").strip().lower()
        if normalized not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        return normalized

    @field_validator("metric")
    @classmethod
    def _check_metric(cls, value: str) -> str:
        normalized = str(value or "cosine").strip().lower()
        if normalized not in VALID_METRICS:
            raise ValueError(f"metric must be one of {VALID_METRICS}")
        return normalized

    @field_validator("index_type")
    @classmethod
    def _check_index_type(cls, value: str) -> str:
        canon = _INDEX_TYPE_CANON.get(str(value or "Flat").strip().lower())
        if canon is None:
            raise ValueError(f"index_type must be one of {VALID_INDEX_TYPES}")
        return canon

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


def validate_knowledge_payload(data: Any) -> dict[str, Any]:
    """Validate and normalize a knowledge payload (``{"bases": [...]}``).

    Fills missing ids and timestamps, dedupes tags, and drops unknown fields.
    Raises ``ValueError`` (with a concise message) on any schema violation or a
    duplicate base id.
    """
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    try:
        payload = KnowledgePayload.model_validate(data)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc)) from exc

    timestamp = now_isoformat()
    seen_ids: set[str] = set()
    bases: list[dict[str, Any]] = []
    for base in payload.bases:
        entry = base.model_dump(mode="json")
        base_id = str(entry.get("id") or "").strip()
        if not base_id:
            base_id = generate_base_id(entry["name"])
            while base_id in seen_ids:
                base_id = generate_base_id(entry["name"])
        if base_id in seen_ids:
            raise ValueError(f"duplicate base id '{base_id}'")
        seen_ids.add(base_id)
        entry["id"] = base_id
        entry["created_at"] = entry.get("created_at") or timestamp
        entry["updated_at"] = entry.get("updated_at") or timestamp

        source_ids: set[str] = set()
        sources: list[dict[str, Any]] = []
        for source in entry.get("sources", []):
            source_id = str(source.get("id") or "").strip() or generate_source_id()
            while source_id in source_ids:
                source_id = generate_source_id()
            source_ids.add(source_id)
            source["id"] = source_id
            sources.append(source)
        entry["sources"] = sources
        bases.append(entry)

    return {"bases": bases}


def validate_knowledge_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/state/knowledge.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_knowledge_payload(data)
