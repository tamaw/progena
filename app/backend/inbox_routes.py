"""Inbox message routes for the backend-owned UI read model.

Inbox messages are written by the runtime agent as ``/inbox/<id>.md`` virtual
files and persist as individual ``inbox_message:<id>`` rows under the
``progenitor.state`` prefix. The backend reads those rows directly and owns the
user-side mutations (mark read/unread, delete), which is safe because the
runtime always reads these documents through the store, never a cache.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .chat_routes import BroadcastUiEvent
from .document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore

INBOX_MESSAGE_KEY_PREFIX = "inbox_message:"


class InboxMessage(BaseModel):
    id: str
    title: str = ""
    created_at: str | None = None
    read: bool = False
    read_at: str | None = None
    body: str = ""


class InboxSnapshotResponse(BaseModel):
    messages: list[InboxMessage]
    total: int
    unread: int


class InboxMarkReadRequest(BaseModel):
    read: bool = True


def _message_key(message_id: str) -> str:
    return f"{INBOX_MESSAGE_KEY_PREFIX}{message_id}"


def _normalize_message(key: str, payload: Any) -> InboxMessage | None:
    if not isinstance(payload, dict):
        return None
    message_id = str(payload.get("id") or key.removeprefix(INBOX_MESSAGE_KEY_PREFIX)).strip()
    if not message_id:
        return None
    return InboxMessage(
        id=message_id,
        title=str(payload.get("title") or ""),
        created_at=str(payload["created_at"]) if payload.get("created_at") else None,
        read=bool(payload.get("read", False)),
        read_at=str(payload["read_at"]) if payload.get("read_at") else None,
        body=str(payload.get("body") or ""),
    )


def list_inbox_messages(store: BackendDocumentStore) -> list[InboxMessage]:
    """Load all inbox messages from the store, newest first."""
    messages = [
        message
        for key, document in store.list_documents(PROGENITOR_STATE_PREFIX, INBOX_MESSAGE_KEY_PREFIX)
        if (message := _normalize_message(key, document.payload)) is not None
    ]
    messages.sort(key=lambda message: message.created_at or "", reverse=True)
    return messages


def inbox_snapshot_response(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
) -> InboxSnapshotResponse:
    messages = list_inbox_messages(get_document_store())
    unread = sum(1 for message in messages if not message.read)
    return InboxSnapshotResponse(messages=messages, total=len(messages), unread=unread)


def broadcast_inbox_snapshot(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
) -> InboxSnapshotResponse:
    response = inbox_snapshot_response(get_document_store=get_document_store)
    broadcast_ui_event("inbox.snapshot", response.model_dump())
    return response


def create_inbox_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    def broadcast_snapshot() -> InboxSnapshotResponse:
        return broadcast_inbox_snapshot(
            get_document_store=get_document_store,
            broadcast_ui_event=broadcast_ui_event,
        )

    def load_message(store: BackendDocumentStore, message_id: str) -> dict[str, Any]:
        document = store.get_document(PROGENITOR_STATE_PREFIX, _message_key(message_id), None)
        if document is None or not isinstance(document.payload, dict):
            raise HTTPException(status_code=404, detail="Inbox message not found")
        return document.payload

    @router.get("/inbox", response_model=InboxSnapshotResponse)
    async def get_inbox():
        """Acknowledge inbox list requests and publish the snapshot on the UI stream."""
        return broadcast_snapshot()

    @router.post("/inbox/{message_id}/read", response_model=InboxMessage)
    async def mark_inbox_message_read(message_id: str, request: InboxMarkReadRequest | None = None):
        """Set one message's read state (read/unread toggle from the UI)."""
        read = request.read if request is not None else True
        store = get_document_store()
        payload = load_message(store, message_id)
        payload["read"] = read
        payload["read_at"] = datetime.now(UTC).isoformat() if read else None
        message = _normalize_message(_message_key(message_id), payload)
        if message is None:
            # load_message already confirmed payload is a dict keyed by this
            # message_id, so normalization only fails here on data corruption.
            raise HTTPException(status_code=500, detail="Inbox message could not be loaded")
        store.save_document(PROGENITOR_STATE_PREFIX, _message_key(message_id), payload)
        broadcast_ui_event("inbox.message.upserted", {"message": message.model_dump()})
        broadcast_snapshot()
        return message

    @router.delete("/inbox/{message_id}", status_code=204)
    async def delete_inbox_message(message_id: str):
        """Delete one inbox message."""
        store = get_document_store()
        load_message(store, message_id)
        store.delete_document(PROGENITOR_STATE_PREFIX, _message_key(message_id))
        broadcast_ui_event("inbox.message.deleted", {"message_id": message_id})
        broadcast_snapshot()
        return None

    return router
