"""Inbox message routes for the backend-owned UI read model.

Inbox messages are written by the runtime agent as ``/inbox/<id>.md`` virtual
files and persist as individual ``inbox_message:<id>`` rows under the
``progena.state`` prefix. The backend reads those rows directly and owns the
user-side mutations (mark read/unread, delete), which is safe because the
runtime always reads these documents through the store, never a cache.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.storage.documents as storage_documents
from src.backend.events import contracts as events_contracts
from src.backend.http.requests import BroadcastUiEvent
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.documents.kinds import INBOX_MESSAGE_KEY_PREFIX


class InboxMessage(BaseModel):
    id: str
    title: str = ""
    created_at: str | None = None
    read: bool = False
    read_at: str | None = None
    body: str = ""
    requires_approval: bool = False
    decision: Literal["approved", "rejected"] | None = None
    task_id: str | None = None
    archived: bool = False


class InboxSnapshotResponse(BaseModel):
    messages: list[InboxMessage]
    total: int
    unread: int


class InboxMarkReadRequest(BaseModel):
    read: bool = True


class InboxArchiveRequest(BaseModel):
    archived: bool = True


class InboxDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


def _message_key(message_id: str) -> str:
    return f"{INBOX_MESSAGE_KEY_PREFIX}{message_id}"


def _normalize_message(key: str, payload: Any) -> InboxMessage | None:
    if not isinstance(payload, dict):
        return None
    message_id = str(payload.get("id") or key.removeprefix(INBOX_MESSAGE_KEY_PREFIX)).strip()
    if not message_id:
        return None
    decision = payload.get("decision")
    return InboxMessage(
        id=message_id,
        title=str(payload.get("title") or ""),
        created_at=str(payload["created_at"]) if payload.get("created_at") else None,
        read=bool(payload.get("read", False)),
        read_at=str(payload["read_at"]) if payload.get("read_at") else None,
        body=str(payload.get("body") or ""),
        requires_approval=bool(payload.get("requires_approval", False)),
        decision=decision if decision in ("approved", "rejected") else None,
        task_id=str(payload["task_id"]) if payload.get("task_id") else None,
        archived=bool(payload.get("archived", False)),
    )


def _inbox_message_sort_key(message: InboxMessage) -> tuple[int, datetime, str]:
    """Order explicit-offset inbox creation instants without rewriting message content."""
    value = message.created_at
    if value and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            if parsed.tzinfo is not None:
                return 1, parsed.astimezone(UTC), ""
    return 0, datetime.min.replace(tzinfo=UTC), value or ""


def list_inbox_messages(store: BackendDocumentStore) -> list[InboxMessage]:
    """Load all inbox messages from the store, newest first."""
    messages = [
        message
        for key, document in store.list_documents(PROGENA_STATE_PREFIX, INBOX_MESSAGE_KEY_PREFIX)
        if (message := _normalize_message(key, document.payload)) is not None
    ]
    messages.sort(key=_inbox_message_sort_key, reverse=True)
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
    request_id: str | None = None,
) -> InboxSnapshotResponse:
    response = inbox_snapshot_response(get_document_store=get_document_store)
    broadcast_ui_event(
        events_contracts.UiEvent.INBOX_SNAPSHOT, response.model_dump(), request_id=request_id
    )
    return response


def create_inbox_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    def broadcast_snapshot(request_id: str | None = None) -> InboxSnapshotResponse:
        return broadcast_inbox_snapshot(
            get_document_store=get_document_store,
            broadcast_ui_event=broadcast_ui_event,
            request_id=request_id,
        )

    def load_message(store: BackendDocumentStore, message_id: str) -> dict[str, Any]:
        document = store.get_document(PROGENA_STATE_PREFIX, _message_key(message_id), None)
        if document is None or not isinstance(document.payload, dict):
            raise HTTPException(status_code=404, detail="Inbox message not found")
        return document.payload

    def mutate_message(
        message_id: str,
        mutate: Callable[[dict[str, Any]], None],
        *,
        request_id: str | None,
    ) -> InboxMessage:
        """Load, mutate, save, and broadcast one inbox message.

        Shared by every single-message mutation route (read/unread, archive,
        decision) -- they differ only in what `mutate` changes on the payload.
        """
        store = get_document_store()
        payload = load_message(store, message_id)
        mutate(payload)
        message = _normalize_message(_message_key(message_id), payload)
        if message is None:
            # load_message already confirmed payload is a dict keyed by this
            # message_id, so normalization only fails here on data corruption.
            raise HTTPException(status_code=500, detail="Inbox message could not be loaded")
        store.save_document(PROGENA_STATE_PREFIX, _message_key(message_id), payload)
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED,
            {"message": message.model_dump()},
            request_id=request_id,
        )
        broadcast_snapshot(request_id=request_id)
        return message

    @router.get("/inbox", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
    async def get_inbox(
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Acknowledge inbox list requests and publish the snapshot on the UI stream."""
        request_id = _request_correlation_id(request, x_request_id)
        broadcast_snapshot(request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_SNAPSHOT]
        )

    @router.post(
        "/inbox/{message_id}/read",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def mark_inbox_message_read(
        http_request: Request,
        message_id: str,
        request: InboxMarkReadRequest | None = None,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Set one message's read state (read/unread toggle from the UI)."""
        request_id = _request_correlation_id(http_request, x_request_id)
        read = request.read if request is not None else True

        def mutate(payload: dict[str, Any]) -> None:
            payload["read"] = read
            payload["read_at"] = datetime.now(UTC).isoformat() if read else None

        mutate_message(message_id, mutate, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_SNAPSHOT]
        )

    @router.post(
        "/inbox/{message_id}/archive",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def archive_inbox_message(
        http_request: Request,
        message_id: str,
        request: InboxArchiveRequest | None = None,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Set one message's archived state (archive/unarchive from the UI)."""
        request_id = _request_correlation_id(http_request, x_request_id)
        archived = request.archived if request is not None else True

        def mutate(payload: dict[str, Any]) -> None:
            payload["archived"] = archived

        mutate_message(message_id, mutate, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_SNAPSHOT]
        )

    @router.post(
        "/inbox/{message_id}/decision",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def decide_inbox_message(
        http_request: Request,
        message_id: str,
        request: InboxDecisionRequest,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Record the user's approve/reject decision on a message."""
        request_id = _request_correlation_id(http_request, x_request_id)

        def mutate(payload: dict[str, Any]) -> None:
            payload["decision"] = request.decision
            payload["read"] = True
            payload["read_at"] = datetime.now(UTC).isoformat()

        mutate_message(message_id, mutate, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_SNAPSHOT]
        )

    @router.delete(
        "/inbox/{message_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_inbox_message(
        message_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Delete one inbox message."""
        request_id = _request_correlation_id(request, x_request_id)
        store = get_document_store()
        load_message(store, message_id)
        store.delete_document(PROGENA_STATE_PREFIX, _message_key(message_id))
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_DELETED,
            {"message_id": message_id},
            request_id=request_id,
        )
        broadcast_snapshot(request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_SNAPSHOT]
        )

    return router


def _broadcast_inbox_snapshot():
    return broadcast_inbox_snapshot(
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
    )
