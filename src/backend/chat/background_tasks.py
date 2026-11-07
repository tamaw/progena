"""Chat-scoped browser projection of durable async-subagent task records."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.backend.events import contracts as events_contracts
from src.backend.events import stream
from src.backend.events.documents import TypedDocumentEvent
from src.backend.http import contracts as http_contracts
from src.backend.http.requests import request_correlation_id
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    get_default_document_store,
)
from src.progena.async_subagents.models import AsyncSubagentTaskRecord
from src.progena.async_subagents.store import ASYNC_SUBAGENT_TASK_KEY_PREFIX


class BackgroundTaskSubscription(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    subscribed: bool = True


class BackgroundTaskEvents:
    """Targeted, versioned snapshots for every chat surface in one browser tab."""

    def __init__(
        self, store_getter: Callable[[], BackendDocumentStore] = get_default_document_store
    ) -> None:
        self._store_getter = store_getter
        self.interests: dict[str, set[str]] = defaultdict(set)
        self.revisions: dict[str, int] = defaultdict(int)
        self._snapshot_lock = asyncio.Lock()

    def unsubscribe(self, client_id: str, thread_id: str | None = None) -> None:
        if thread_id is None:
            self.interests.pop(client_id, None)
            return
        threads = self.interests.get(client_id)
        if threads is None:
            return
        threads.discard(thread_id)
        if not threads:
            del self.interests[client_id]

    def subscribe(self, client_id: str, thread_id: str) -> None:
        self.interests[client_id].add(thread_id)

    async def snapshot(
        self, store: BackendDocumentStore, thread_id: str
    ) -> tuple[list[dict[str, Any]], int]:
        # Serializing against document updates guarantees an older store read
        # cannot arrive after a newer targeted update.
        async with self._snapshot_lock:
            tasks = await asyncio.to_thread(_tasks_for_thread, store, thread_id)
            return tasks, self.revisions[thread_id]

    async def document_event(self, typed: TypedDocumentEvent) -> None:
        event = typed.event
        if event.prefix != PROGENA_STATE_PREFIX or not event.key.startswith(
            ASYNC_SUBAGENT_TASK_KEY_PREFIX
        ):
            return
        async with self._snapshot_lock:
            # A document event is historical: the durable records may already
            # contain a terminal state from a later write.
            by_thread = await asyncio.to_thread(_all_tasks_by_thread, self._store_getter())
            watched = {thread_id for threads in self.interests.values() for thread_id in threads}
            revisions = {
                thread_id: max(self.revisions[thread_id] + 1, int(getattr(event, "seq", 0) or 0))
                for thread_id in watched
            }
            self.revisions.update(revisions)
            deliveries = [
                (client_id, thread_id, by_thread.get(thread_id, []), revisions[thread_id])
                for client_id, threads in self.interests.items()
                for thread_id in threads
            ]
        for client_id, thread_id, tasks, revision in deliveries:
            stream.broadcast_ui_event(
                events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_UPDATED,
                {"thread_id": thread_id, "tasks": tasks, "revision": revision},
                scope="chat",
                session_id=thread_id,
                client_id=client_id,
            )


background_task_events = BackgroundTaskEvents()


def _tasks_by_thread(payload: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(payload, list):
        return grouped
    for item in payload:
        try:
            task = AsyncSubagentTaskRecord.model_validate(item)
        except (TypeError, ValueError):
            continue
        if task.parent_thread_id:
            grouped[task.parent_thread_id].append(task.model_dump(mode="json"))
    for tasks in grouped.values():
        tasks.sort(key=lambda task: (task.get("created_at") or "", task["task_id"]))
    return grouped


def _all_tasks_by_thread(store: BackendDocumentStore) -> dict[str, list[dict[str, Any]]]:
    rows = store.list_documents(PROGENA_STATE_PREFIX, ASYNC_SUBAGENT_TASK_KEY_PREFIX)
    return _tasks_by_thread([document.payload for _key, document in rows])


def _tasks_for_thread(store: BackendDocumentStore, thread_id: str) -> list[dict[str, Any]]:
    return _all_tasks_by_thread(store).get(thread_id, [])


def _require_stream(client_id: str) -> None:
    if client_id not in stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="An active UI stream is required")


router = APIRouter(prefix="/chats", tags=["chat"])


@router.get(
    "/{thread_id}/background-tasks",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_background_tasks(
    thread_id: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> http_contracts.UiRequestAcknowledgement:
    """Publish the current durable async-task snapshot for one chat."""
    _require_stream(client_id)
    request_id = request_correlation_id(request, x_request_id)
    tasks, revision = await background_task_events.snapshot(get_default_document_store(), thread_id)
    stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SNAPSHOT,
        {"thread_id": thread_id, "tasks": tasks, "revision": revision},
        scope="chat",
        session_id=thread_id,
        client_id=client_id,
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SNAPSHOT]
    )


@router.put(
    "/{thread_id}/background-tasks/subscription",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def put_background_tasks_subscription(
    thread_id: str,
    body: BackgroundTaskSubscription,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> http_contracts.UiRequestAcknowledgement:
    _require_stream(body.client_id)
    event_types: list[str]
    request_id = request_correlation_id(request, x_request_id)
    if body.subscribed:
        background_task_events.subscribe(body.client_id, thread_id)
        tasks, revision = await background_task_events.snapshot(
            get_default_document_store(), thread_id
        )
        stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SNAPSHOT,
            {"thread_id": thread_id, "tasks": tasks, "revision": revision},
            scope="chat",
            session_id=thread_id,
            client_id=body.client_id,
            request_id=request_id,
        )
        event_types = [events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SNAPSHOT]
    else:
        background_task_events.unsubscribe(body.client_id, thread_id)
        event_types = []
    stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SUBSCRIPTION_UPDATED,
        {"thread_id": thread_id, "subscribed": body.subscribed},
        scope="chat",
        session_id=thread_id,
        client_id=body.client_id,
        request_id=request_id,
    )
    event_types.append(events_contracts.UiEvent.CHAT_BACKGROUND_TASKS_SUBSCRIPTION_UPDATED)
    return http_contracts.acknowledgement_response(request_id, event_types)
