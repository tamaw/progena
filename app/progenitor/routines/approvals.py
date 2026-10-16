"""Native human interrupts and shared UI/chat decision validation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from weakref import WeakValueDictionary

DECISION_PREFIX = "routine_approval:"
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def nested_approval_context(interrupts: Iterable[Any], context: dict[str, Any]) -> dict[str, Any]:
    """Recover child invocation provenance before an interrupted node replays.

    LangGraph restarts a node on resume. The checkpointed interrupt, rather
    than a new child invocation, must supply the original child thread.
    """
    children = {}
    for item in interrupts:
        value = getattr(item, "value", None)
        if isinstance(value, dict) and value.get("child_invocation"):
            children[value["node"]] = value
    result = {key: value for key, value in context.items() if key != "_routine_pending_children"}
    if children:
        result["_routine_pending_children"] = children
    return result


def approval_resume_value(interrupts: Iterable[Any], interrupt_id: str, answer: Any) -> Any:
    """Bind a nested answer to its original child approval during node replay."""
    for item in interrupts:
        if (
            item.id == interrupt_id
            and isinstance(item.value, dict)
            and item.value.get("child_invocation")
        ):
            return {"_routine_nested_answer": answer, "approval": item.value}
    return answer


def approval_decision_id(item: Any) -> str:
    """A task may pause repeatedly; each nested approval is a new decision."""
    if not isinstance(item.value, dict) or not item.value.get("child_invocation"):
        return item.id
    identity = json.dumps(approval_identity(item.value), sort_keys=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"{item.id}:{digest}"


def approval_identity(value: Any) -> Any:
    """Identify the approval chain independently of replay request metadata."""
    if not isinstance(value, dict) or not value.get("child_invocation"):
        return value
    return (
        value["node"],
        value["child_invocation"]["thread_id"],
        value["child_interrupt_id"],
        approval_identity(value["child_approval"]),
    )


def decision_key(thread_id: str, interrupt_id: str) -> str:
    return f"{DECISION_PREFIX}{thread_id}:{interrupt_id}"


def _approver(value: Any) -> str:
    """Who may answer: a nested routine's pause carries its own human node's rule."""
    if isinstance(value, dict) and value.get("child_approval"):
        return _approver(value["child_approval"])
    return value.get("approver", "any") if isinstance(value, dict) else "any"


def pending_approvals(interrupts: Iterable[Any]) -> list[dict[str, Any]]:
    approvals = {}
    for interrupt in interrupts:
        value = getattr(interrupt, "value", None)
        interrupt_id = getattr(interrupt, "id", None)
        if not interrupt_id or not isinstance(value, dict) or not value.get("node"):
            continue
        decision_id = approval_decision_id(interrupt)
        approvals[decision_id] = {
            "interrupt_id": decision_id,
            "resume_interrupt_id": interrupt_id,
            "node_id": value["node"],
            "message": value.get("message", ""),
            "allowed_responses": value.get("allowed_responses"),
            "approver": _approver(value),
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
