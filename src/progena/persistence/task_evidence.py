"""Immutable, content-addressed task evidence stored outside state documents."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from .store import LangGraphRuntimeStore

TASK_EVIDENCE_NAMESPACE = ("progena", "task_evidence")


class TaskEvidenceReference(BaseModel):
    """Compact pointer to an immutable task payload."""

    namespace: tuple[str, str] = TASK_EVIDENCE_NAMESPACE
    key: str
    sha256: str
    size_bytes: int = Field(ge=0)


def _canonical_payload(payload: Any) -> tuple[Any, bytes]:
    """Return JSON-safe content and its stable UTF-8 representation."""
    serialized = json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return json.loads(serialized), serialized.encode("utf-8")


async def archive_task_evidence(
    store: LangGraphRuntimeStore,
    payload: Any,
) -> TaskEvidenceReference:
    """Persist *payload* once and return its deterministic content reference."""
    normalized, encoded = _canonical_payload(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    key = f"sha256:{digest}"
    await store.save_immutable_native_document(TASK_EVIDENCE_NAMESPACE, key, normalized)
    return TaskEvidenceReference(key=key, sha256=digest, size_bytes=len(encoded))


async def archive_task_record(
    store: LangGraphRuntimeStore,
    *,
    task_kind: str,
    task_id: str,
    record: dict[str, Any],
) -> TaskEvidenceReference:
    """Archive a pruned record with a task-id index in the evidence namespace."""
    archive = {"task_kind": task_kind, "task_id": task_id, "record": record}
    normalized, encoded = _canonical_payload(archive)
    digest = hashlib.sha256(encoded).hexdigest()
    key = f"sha256:{digest}"
    await store.save_immutable_native_document(
        TASK_EVIDENCE_NAMESPACE,
        key,
        normalized,
        index={"task_archive": True, "task_kind": task_kind, "task_id": task_id},
    )
    return TaskEvidenceReference(key=key, sha256=digest, size_bytes=len(encoded))


async def load_archived_task_record(
    store: LangGraphRuntimeStore,
    *,
    task_kind: str,
    task_id: str,
) -> dict[str, Any] | None:
    """Return a task's archived compact record after terminal retention expires."""
    rows = await store.list_native_documents(
        TASK_EVIDENCE_NAMESPACE,
        filter={"task_archive": True, "task_kind": task_kind, "task_id": task_id},
    )
    archives = [
        dict(payload["record"])
        for _key, payload in rows
        if (
            isinstance(payload, dict)
            and payload.get("task_kind") == task_kind
            and payload.get("task_id") == task_id
            and isinstance(payload.get("record"), dict)
        )
    ]
    if not archives:
        return None
    return max(archives, key=lambda record: str(record.get("updated_at") or ""))


async def load_task_evidence(
    store: LangGraphRuntimeStore,
    reference: TaskEvidenceReference | dict[str, Any] | None,
) -> Any | None:
    """Load evidence by compact reference without creating a missing document."""
    if reference is None:
        return None
    try:
        parsed = (
            reference
            if isinstance(reference, TaskEvidenceReference)
            else TaskEvidenceReference.model_validate(reference)
        )
    except (TypeError, ValueError):
        return None
    if parsed.namespace != TASK_EVIDENCE_NAMESPACE:
        return None
    return await store.peek_native_document(parsed.namespace, parsed.key)
