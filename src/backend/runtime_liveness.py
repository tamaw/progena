"""Read the runtime process identity recorded in its health document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.runtime import ownership


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
    """Whether durable work belongs to an earlier runtime process (see ``ownership``)."""
    if live is None:
        return False
    return ownership.owner_is_gone(owner_instance, started_at, live.instance_id, live.started_at)
