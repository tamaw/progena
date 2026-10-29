"""Whether durable work was cut off by a runtime restart.

One rule shared by the backend (which learns the live runtime from the health
document) and the runtime itself (which is the live runtime). A run is
interrupted when it was still executing and the process that owned it is gone;
nothing about it is persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Routine lifecycle statuses that mean the attempt was still executing.
EXECUTING_ROUTINE_STATUSES = frozenset({"started", "stopping", "cancelling"})


def owner_is_gone(
    owner_instance: str | None,
    started_at: str | None,
    live_instance: str | None,
    live_started_at: str | None,
) -> bool:
    """Whether work owned by ``owner_instance`` belongs to an earlier runtime process.

    An unknown live runtime is deliberately inconclusive. Records written before
    ownership was stamped are classified by comparing their start time with the
    live process's start time.
    """
    if not live_instance or not live_started_at:
        return False
    if owner_instance:
        return owner_instance != live_instance
    started = parse_timestamp(started_at)
    live_started = parse_timestamp(live_started_at)
    return bool(started and live_started and started < live_started)


def routine_attempt_interrupted(
    status: str | None,
    started_event: dict[str, Any] | None,
    live_instance: str | None,
    live_started_at: str | None,
) -> bool:
    """A routine attempt whose latest status is still executing and whose owner is gone."""
    if status not in EXECUTING_ROUTINE_STATUSES or started_event is None:
        return False
    owner = started_event.get("runtime_instance")
    started_at = started_event.get("timestamp")
    return owner_is_gone(
        owner if isinstance(owner, str) else None,
        started_at if isinstance(started_at, str) else None,
        live_instance,
        live_started_at,
    )


def schedule_run_interrupted(
    record: dict[str, Any], live_instance: str | None, live_started_at: str | None
) -> bool:
    """A scheduled run still running (or claimed) whose owner is gone."""
    status = str(record.get("status") or "queued").lower()
    started_at = record.get("started_at")
    if not (status == "running" or (status == "queued" and started_at)):
        return False
    owner = record.get("runtime_instance")
    return owner_is_gone(
        owner if isinstance(owner, str) else None,
        started_at if isinstance(started_at, str) else None,
        live_instance,
        live_started_at,
    )


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO-8601 (``Z`` or offset, any precision); naive values are UTC."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
