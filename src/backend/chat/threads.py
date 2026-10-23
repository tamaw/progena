"""In-memory registry for conversation threads and workspace executions."""

from __future__ import annotations

from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field

from src.progena.utils import utcnow_isoformat

InvocationType = Literal["user", "automation", "system", "workflow_step"]
ThreadStatus = Literal["running", "completed", "error"]


class ThreadRecord(BaseModel):
    """Tracked metadata for a conversation thread or execution thread."""

    thread_id: str
    invocation_type: InvocationType = "automation"
    invocation_source: str | None = None
    root_widget_id: str | None = None
    widget_ids: list[str] = Field(default_factory=list)
    status: ThreadStatus = "running"
    created_at: str
    updated_at: str
    last_response: str | None = None
    last_error: str | None = None


class ThreadsResponse(BaseModel):
    """Response model for listing tracked threads."""

    items: list[ThreadRecord]
    total: int


_threads: dict[str, ThreadRecord] = {}
_lock = Lock()


def upsert_thread(
    thread_id: str,
    *,
    invocation_type: InvocationType = "automation",
    invocation_source: str | None = None,
    root_widget_id: str | None = None,
    widget_id: str | None = None,
    status: ThreadStatus = "running",
) -> ThreadRecord:
    """Create or update a tracked thread record."""

    now = utcnow_isoformat()
    with _lock:
        record = _threads.get(thread_id)
        if record is None:
            record = ThreadRecord(
                thread_id=thread_id,
                invocation_type=invocation_type,
                invocation_source=invocation_source,
                root_widget_id=root_widget_id or widget_id,
                widget_ids=[widget_id] if widget_id else [],
                status=status,
                created_at=now,
                updated_at=now,
            )
            _threads[thread_id] = record
            return record

        record.invocation_type = invocation_type
        if invocation_source:
            record.invocation_source = invocation_source
        if root_widget_id:
            record.root_widget_id = root_widget_id
        if widget_id and widget_id not in record.widget_ids:
            record.widget_ids.append(widget_id)
        record.status = status
        record.updated_at = now
        return record


def complete_thread(thread_id: str, response: str, widget_id: str | None = None) -> ThreadRecord:
    """Mark a thread as completed and record its latest response."""

    record = upsert_thread(thread_id, widget_id=widget_id, status="completed")
    record.last_response = response
    record.last_error = None
    record.updated_at = utcnow_isoformat()
    return record


def fail_thread(thread_id: str, error: str, widget_id: str | None = None) -> ThreadRecord:
    """Mark a thread as failed and record its latest error."""

    record = upsert_thread(thread_id, widget_id=widget_id, status="error")
    record.last_error = error
    record.updated_at = utcnow_isoformat()
    return record


def list_threads(invocation_type: InvocationType | None = None) -> list[ThreadRecord]:
    """Return tracked threads, optionally filtered by invocation type."""

    with _lock:
        records = list(_threads.values())

    if invocation_type is not None:
        records = [record for record in records if record.invocation_type == invocation_type]

    return sorted(records, key=lambda record: record.updated_at, reverse=True)


def clear_threads() -> None:
    """Clear all tracked threads. Intended for tests."""

    with _lock:
        _threads.clear()
