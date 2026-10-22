"""Enqueue deterministic routine runs onto the runtime's background queue.

The backend writes a ``metadata.kind = "routine_run"`` task onto the shared
Postgres-backed ``background_tasks`` document; the runtime's heartbeat graph
routes it straight to ``routine_run_node``, which runs the routine without an
LLM deciding whether to. Progress is reported as immutable
``routine_run_event:<request_id>:<status>`` documents carrying the run's
``session_id`` (also the last segment of its routine ``thread_id``).
"""

from __future__ import annotations

import uuid
from typing import Any

from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    store_database_url,
)
from src.progena.routines.events import RUN_EVENT_PREFIX
from src.progena.utils import utcnow_isoformat


def append_background_task(doc_store: BackendDocumentStore, task: dict[str, Any]) -> None:
    """Atomically append a task to the shared background_tasks document.

    Uses a Postgres advisory lock so concurrent backend enqueues and runtime
    dequeues cannot lose or duplicate tasks.
    """
    url = store_database_url(doc_store)
    if not url:
        existing = doc_store.get_document(PROGENA_STATE_PREFIX, "background_tasks", [])
        tasks = list(existing.payload) if existing is not None else []
        tasks.append(task)
        doc_store.save_document(PROGENA_STATE_PREFIX, "background_tasks", tasks)
        return

    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtextextended('background_tasks', 0))")
        try:
            existing = doc_store.get_document(PROGENA_STATE_PREFIX, "background_tasks", [])
            tasks = list(existing.payload) if existing is not None else []
            tasks.append(task)
            doc_store.save_document(PROGENA_STATE_PREFIX, "background_tasks", tasks)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended('background_tasks', 0))")


def enqueue_routine_run(
    doc_store: BackendDocumentStore,
    routine_id: str,
    *,
    request_id: str,
    session_id: str | None = None,
    parameters: dict[str, Any] | None = None,
    message: str | None = None,
    target_node: str | None = None,
) -> dict[str, Any]:
    """Queue one routine run and record its ``enqueued`` lifecycle event.

    ``parameters`` are the routine's declared ``input_schema`` fields (callers
    validate them); ``message`` is the conversational input for routines that
    declare none. Returns the lifecycle payload written.
    """

    session_id = session_id or uuid.uuid4().hex
    metadata: dict[str, Any] = {
        "kind": "routine_run",
        "routine_id": routine_id,
        "session_id": session_id,
        "request_id": request_id,
    }
    if target_node:
        metadata["target_node"] = target_node
    if parameters:
        metadata["parameters"] = parameters
    if message is not None:
        metadata["message"] = message
    queued_at = utcnow_isoformat()
    append_background_task(
        doc_store,
        {
            "id": f"routine-run-{uuid.uuid4().hex}",
            "content": f'Run the "{routine_id}" routine graph.',
            "metadata": metadata,
            "created_at": queued_at,
        },
    )
    lifecycle = {
        "agent_id": routine_id,
        "session_id": session_id,
        "request_id": request_id,
        "status": "enqueued",
        "timestamp": queued_at,
    }
    doc_store.save_document(
        PROGENA_STATE_PREFIX, f"{RUN_EVENT_PREFIX}{request_id}:enqueued", lifecycle
    )
    return lifecycle
