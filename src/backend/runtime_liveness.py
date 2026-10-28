"""Read the runtime process identity recorded in its health document."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore


@dataclass(frozen=True)
class LiveRuntime:
    """Identity and start time advertised by the presently live runtime."""

    instance_id: str
    started_at: str


def live_runtime(store: BackendDocumentStore) -> LiveRuntime | None:
    """Return the runtime identity from health, or ``None`` when unavailable."""
    try:
        document = store.get_document(PROGENA_STATE_PREFIX, "health", {})
    except Exception:
        return None
    payload: Any = document.payload if document is not None else None
    if not isinstance(payload, dict):
        return None
    instance_id = payload.get("runtime_instance")
    started_at = payload.get("started_at")
    if not isinstance(instance_id, str) or not instance_id:
        return None
    if not isinstance(started_at, str) or not started_at:
        return None
    return LiveRuntime(instance_id=instance_id, started_at=started_at)


def owner_is_gone(
    owner_instance: str | None,
    started_at: str | None,
    live: LiveRuntime | None,
) -> bool:
    """Whether durable work belongs to an earlier runtime process.

    A missing live identity is deliberately inconclusive. Older records without
    an owner can only be classified after comparing their recorded start time to
    the currently advertised process start time.
    """
    if live is None:
        return False
    if owner_instance:
        return owner_instance != live.instance_id
    started = _parse_timestamp(started_at)
    live_started = _parse_timestamp(live.started_at)
    return bool(started and live_started and started < live_started)


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse ISO-8601 (``Z`` or offset, any precision); naive values are UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
