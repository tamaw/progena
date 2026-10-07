"""Native human interrupts and shared UI/chat decision validation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from weakref import WeakValueDictionary

DECISION_PREFIX = "routine_approval:"
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def decision_key(thread_id: str, interrupt_id: str) -> str:
    return f"{DECISION_PREFIX}{thread_id}:{interrupt_id}"


def pending_approvals(interrupts: Iterable[Any]) -> list[dict[str, Any]]:
    approvals = {}
    for interrupt in interrupts:
        value = getattr(interrupt, "value", None)
        interrupt_id = getattr(interrupt, "id", None)
        if not interrupt_id or not isinstance(value, dict) or not value.get("node"):
            continue
        approvals[interrupt_id] = {
            "interrupt_id": interrupt_id,
            "node_id": value["node"],
            "message": value.get("message", ""),
            "allowed_responses": value.get("allowed_responses"),
        }
    return list(approvals.values())


def validate_answer(
    approvals: list[dict[str, Any]], answer: Any, interrupt_id: str | None = None
) -> dict[str, Any]:
    if interrupt_id is None and len(approvals) != 1:
        raise ValueError("Select a pending human approval before answering")
    approval = next(
        (
            item
            for item in approvals
            if interrupt_id is None or item["interrupt_id"] == interrupt_id
        ),
        None,
    )
    if approval is None:
        raise ValueError("This approval is no longer pending")
    allowed = approval["allowed_responses"]
    if allowed is not None and answer not in allowed:
        raise ValueError(f"Response must be one of {allowed!r}")
    return approval


@asynccontextmanager
async def approval_lock(
    thread_id: str, database_url: str | None = None, *, wait: bool = False
) -> AsyncIterator[None]:
    """Serialize answers across UI/chat. Postgres locks span both processes."""
    if database_url:
        from psycopg import AsyncConnection

        async with await AsyncConnection.connect(database_url, autocommit=True) as conn:
            function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
            result = await conn.execute(f"SELECT {function}(hashtextextended(%s, 0))", (thread_id,))
            row = await result.fetchone()
            if not wait and (not row or not row[0]):
                raise ValueError("An answer for this run is already being processed")
            # Closing this dedicated connection releases its session lock.
            yield
    else:
        lock = _locks.setdefault(thread_id, asyncio.Lock())
        if not wait and lock.locked():
            raise ValueError("An answer for this run is already being processed")
        async with lock:
            yield
