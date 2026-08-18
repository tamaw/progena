"""Versioned routine registry persisted through the backend document store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.backend.document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from app.progenitor.utils import utcnow_isoformat

ROUTINE_KEY_PREFIX = "routine:"
ROUTINE_REVISION_KEY_PREFIX = "routine_revision:"


class RoutineRevision(BaseModel):
    """An immutable, executable revision of a routine."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    title: str
    description: str = ""
    category: str = "general"
    icon: str | None = None
    prompt_template: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    published_at: str


class Routine(BaseModel):
    """Routine identity and its current draft/published state."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    status: Literal["draft", "published"] = "draft"
    title: str
    description: str = ""
    category: str = "general"
    icon: str | None = None
    prompt_template: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    published_revision: int | None = None
    created_at: str
    updated_at: str


def _key(routine_id: str) -> str:
    return f"{ROUTINE_KEY_PREFIX}{routine_id}"


def _revision_key(routine_id: str, revision: int) -> str:
    return f"{ROUTINE_REVISION_KEY_PREFIX}{routine_id}:{revision}"


def _payload(document: Any) -> dict[str, Any] | None:
    payload = getattr(document, "payload", document)
    return payload if isinstance(payload, dict) else None


def list_routines(store: BackendDocumentStore) -> list[Routine]:
    routines: list[Routine] = []
    for _key_name, document in store.list_documents(PROGENITOR_STATE_PREFIX, ROUTINE_KEY_PREFIX):
        payload = _payload(document)
        if payload is not None:
            routines.append(Routine.model_validate(payload))
    return sorted(routines, key=lambda item: item.updated_at, reverse=True)


def get_routine(store: BackendDocumentStore, routine_id: str) -> Routine | None:
    document = store.get_document(PROGENITOR_STATE_PREFIX, _key(routine_id), None)
    payload = _payload(document)
    return Routine.model_validate(payload) if payload is not None else None


def get_routine_revision(
    store: BackendDocumentStore, routine_id: str, revision: int
) -> RoutineRevision | None:
    document = store.get_document(
        PROGENITOR_STATE_PREFIX, _revision_key(routine_id, revision), None
    )
    payload = _payload(document)
    return RoutineRevision.model_validate(payload) if payload is not None else None


def save_routine(
    store: BackendDocumentStore, payload: Mapping[str, Any], *, publish: bool = False
) -> Routine:
    """Save a draft and optionally atomically publish a new immutable revision."""

    now = utcnow_isoformat()
    routine_id = str(payload.get("id") or "").strip()
    existing = get_routine(store, routine_id) if routine_id else None
    normalized = {
        **dict(payload),
        "id": routine_id,
        "created_at": existing.created_at if existing else now,
        "updated_at": now,
        "published_revision": existing.published_revision if existing else None,
        "status": "published"
        if publish
        else str(payload.get("status") or (existing.status if existing else "draft")),
    }
    routine = Routine.model_validate(normalized)
    if publish:
        revision = (existing.published_revision or 0) + 1 if existing else 1
        published = RoutineRevision(
            id=routine.id,
            revision=revision,
            title=routine.title,
            description=routine.description,
            category=routine.category,
            icon=routine.icon,
            prompt_template=routine.prompt_template,
            input_schema=routine.input_schema,
            output_schema=routine.output_schema,
            allowed_tools=routine.allowed_tools,
            published_at=now,
        )
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            _revision_key(routine.id, revision),
            published.model_dump(mode="json"),
        )
        routine = routine.model_copy(update={"status": "published", "published_revision": revision})
    store.save_document(PROGENITOR_STATE_PREFIX, _key(routine.id), routine.model_dump(mode="json"))
    return routine
