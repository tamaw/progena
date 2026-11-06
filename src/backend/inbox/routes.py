"""Inbox list/detail routes and privacy-preserving live projections."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

import src.backend.events.stream as events_stream
import src.backend.storage.documents as storage_documents
from src.backend.events import contracts as events_contracts
from src.backend.events.documents import TypedDocumentEvent
from src.backend.http import contracts as http_contracts
from src.backend.http.requests import BroadcastUiEvent
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.inbox.events import inbox_message_events
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    InboxRowFilter,
    InboxRowQuery,
    InboxRowSort,
)
from src.progena.documents.kinds import INBOX_MESSAGE_KEY_PREFIX

_INBOX_ROUTE_ORIGIN_PREFIX = "inbox-route:"
INBOX_LIST_PAGE_SIZE = 50
INBOX_LIST_MAX_PAGE_SIZE = 200
# Tabs on the Inbox page. Row upserts and deletions go only to these tabs; the
# unread badge (`inbox.counts.updated`) stays global app chrome.
INBOX_TOPIC = "inbox"


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


class InboxMessageRow(BaseModel):
    id: str
    title: str = ""
    created_at: str | None = None
    read: bool = False
    read_at: str | None = None
    requires_approval: bool = False
    decision: Literal["approved", "rejected"] | None = None
    task_id: str | None = None
    archived: bool = False
    preview: str = ""


class InboxListView(BaseModel):
    q: str = ""
    filter: InboxRowFilter = "all"
    sort: InboxRowSort = "recent"


class InboxListPage(BaseModel):
    view: InboxListView
    offset: int
    rows: list[InboxMessageRow]
    total: int


class InboxMarkReadRequest(BaseModel):
    read: bool = True


class InboxArchiveRequest(BaseModel):
    archived: bool = True


class InboxDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class InboxSubscriptionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    message_id: str | None = Field(default=None, min_length=1, max_length=512)
    # False leaves the Inbox page entirely; omitted keeps the current topic state.
    watching: bool | None = None


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


def _row_from_payload(key: str, payload: Any) -> InboxMessageRow | None:
    message = _normalize_message(key, payload)
    if message is None:
        return None
    preview = str(payload.get("preview") or "") if isinstance(payload, dict) else ""
    return InboxMessageRow(**message.model_dump(exclude={"body"}), preview=preview)


def query_inbox_list(
    store: BackendDocumentStore, view: InboxListView, *, offset: int, limit: int
) -> InboxListPage:
    """Read one SQL-backed slim page; the full body never crosses this boundary."""

    page = store.query_inbox_rows(
        InboxRowQuery(filter=view.filter, sort=view.sort, q=view.q.strip()),
        offset=offset,
        limit=limit,
    )
    rows = [
        row
        for payload in page.rows
        if (row := _row_from_payload(_message_key(str(payload.get("id") or "")), payload))
        is not None
    ]
    return InboxListPage(view=view, offset=offset, rows=rows, total=page.total)


def _route_request_id(origin: str | None) -> str | None:
    if origin is None or not origin.startswith(_INBOX_ROUTE_ORIGIN_PREFIX):
        return None
    return origin.removeprefix(_INBOX_ROUTE_ORIGIN_PREFIX) or None


def _require_stream(client_id: str) -> None:
    if client_id not in events_stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="Inbox requests require an active UI stream")


def _publish_counts(
    store: BackendDocumentStore,
    broadcast_ui_event: BroadcastUiEvent,
    *,
    unread: int | None = None,
    request_id: str | None = None,
    client_id: str | None = None,
) -> None:
    broadcast_ui_event(
        events_contracts.UiEvent.INBOX_COUNTS_UPDATED,
        {"unread": store.inbox_unread_count() if unread is None else unread},
        request_id=request_id,
        client_id=client_id,
    )


def publish_inbox_message_change(
    store: BackendDocumentStore,
    message: InboxMessage,
    *,
    broadcast_ui_event: BroadcastUiEvent = events_stream.broadcast_ui_event,
    unread: int | None = None,
    request_id: str | None = None,
    client_id: str | None = None,
) -> None:
    """Publish a slim row to the requester and uncorrelated Inbox-page updates."""
    row = InboxMessageRow(**message.model_dump(exclude={"body"}), preview=_preview(message.body))
    payload = {"message": row.model_dump()}
    if client_id is not None:
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED,
            payload,
            request_id=request_id,
            client_id=client_id,
        )
    broadcast_ui_event(
        events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED,
        payload,
        topic=INBOX_TOPIC,
        exclude_client_id=client_id,
    )
    _publish_counts(store, broadcast_ui_event, unread=unread)
    for interest in inbox_message_events.interested_in(message.id):
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_UPDATED,
            {"message": message.model_dump()},
            client_id=interest.client_id,
            interest=interest.token,
        )


def publish_inbox_message_deleted(
    store: BackendDocumentStore,
    message_id: str,
    *,
    broadcast_ui_event: BroadcastUiEvent = events_stream.broadcast_ui_event,
    unread: int | None = None,
    request_id: str | None = None,
    client_id: str | None = None,
) -> None:
    payload = {"message_id": message_id}
    if client_id is not None:
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_DELETED,
            payload,
            request_id=request_id,
            client_id=client_id,
        )
    broadcast_ui_event(
        events_contracts.UiEvent.INBOX_MESSAGE_DELETED,
        payload,
        topic=INBOX_TOPIC,
        exclude_client_id=client_id,
    )
    _publish_counts(store, broadcast_ui_event, unread=unread)


def _preview(body: str) -> str:
    for line in body.splitlines():
        preview = line.strip()
        if preview:
            return preview[:200]
    return ""


async def publish_inbox_document_event(typed: TypedDocumentEvent) -> None:
    """Project runtime inbox writes without ever replaying a full inbox snapshot."""

    event = typed.event
    if event.prefix != PROGENA_STATE_PREFIX or not event.key.startswith(INBOX_MESSAGE_KEY_PREFIX):
        return
    if _route_request_id(event.origin) is not None:
        return
    store = storage_documents.get_default_document_store()
    request_id = _route_request_id(event.origin)
    unread = await asyncio.to_thread(store.inbox_unread_count)
    message_id = event.key.removeprefix(INBOX_MESSAGE_KEY_PREFIX)
    if event.op == "deleted":
        publish_inbox_message_deleted(store, message_id, unread=unread, request_id=request_id)
        return
    message = _normalize_message(event.key, event.after)
    if message is not None:
        publish_inbox_message_change(store, message, unread=unread, request_id=request_id)


def create_inbox_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    def mutate_message(message_id: str, patch: dict[str, Any], *, request_id: str) -> InboxMessage:
        store = get_document_store()

        def apply(payload: Any) -> dict[str, Any]:
            if not isinstance(payload, dict):
                raise HTTPException(status_code=404, detail="Inbox message not found")
            return {**payload, **patch}

        with storage_documents.document_write_origin(f"{_INBOX_ROUTE_ORIGIN_PREFIX}{request_id}"):
            payload = store.mutate_document(PROGENA_STATE_PREFIX, _message_key(message_id), apply)
        message = _normalize_message(_message_key(message_id), payload)
        if message is None:
            raise HTTPException(status_code=500, detail="Inbox message could not be loaded")
        return message

    def delete_message(message_id: str, *, request_id: str) -> bool:
        with storage_documents.document_write_origin(f"{_INBOX_ROUTE_ORIGIN_PREFIX}{request_id}"):
            deleted = get_document_store().pop_document(
                PROGENA_STATE_PREFIX, _message_key(message_id)
            )
        return isinstance(deleted, dict)

    @router.get("/inbox", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
    async def get_inbox(
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        q: str = Query(default="", max_length=200),
        list_filter: InboxRowFilter = Query(default="all", alias="filter"),
        sort: InboxRowSort = "recent",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=INBOX_LIST_PAGE_SIZE, ge=0, le=INBOX_LIST_MAX_PAGE_SIZE),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        events_stream.subscribe_topic(client_id, INBOX_TOPIC)
        request_id = _request_correlation_id(request, x_request_id)
        view = InboxListView(q=q, filter=list_filter, sort=sort)
        page = await asyncio.to_thread(
            query_inbox_list, get_document_store(), view, offset=offset, limit=limit
        )
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_LIST_LOADED,
            page.model_dump(mode="json"),
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_LIST_LOADED]
        )

    @router.get(
        "/inbox/counts", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
    async def get_inbox_counts(
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        request_id = _request_correlation_id(request, x_request_id)
        store = get_document_store()
        unread = await asyncio.to_thread(store.inbox_unread_count)
        _publish_counts(
            store, broadcast_ui_event, unread=unread, request_id=request_id, client_id=client_id
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_COUNTS_UPDATED]
        )

    @router.get(
        "/inbox/{message_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_inbox_message(
        message_id: str,
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        events_stream.subscribe_topic(client_id, INBOX_TOPIC)
        request_id = _request_correlation_id(request, x_request_id)
        document = await asyncio.to_thread(
            get_document_store().get_document, PROGENA_STATE_PREFIX, _message_key(message_id)
        )
        message = _normalize_message(
            _message_key(message_id), document.payload if document else None
        )
        if message is None:
            raise HTTPException(status_code=404, detail="Inbox message not found")
        inbox_message_events.subscribe(client_id, message_id)
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_SELECTED,
            {"message": message.model_dump()},
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_SELECTED]
        )

    @router.put(
        "/inbox/subscription",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def put_inbox_subscription(
        body: InboxSubscriptionRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(body.client_id)
        if body.watching is False:
            events_stream.unsubscribe_topic(body.client_id, INBOX_TOPIC)
        else:
            events_stream.subscribe_topic(body.client_id, INBOX_TOPIC)
        request_id = _request_correlation_id(request, x_request_id)
        if body.message_id is None:
            inbox_message_events.unsubscribe(body.client_id)
            store = get_document_store()
            unread = await asyncio.to_thread(store.inbox_unread_count)
            _publish_counts(
                store,
                broadcast_ui_event,
                unread=unread,
                request_id=request_id,
                client_id=body.client_id,
            )
            return http_contracts.acknowledgement_response(
                request_id, [events_contracts.UiEvent.INBOX_COUNTS_UPDATED]
            )

        document = await asyncio.to_thread(
            get_document_store().get_document,
            PROGENA_STATE_PREFIX,
            _message_key(body.message_id),
        )
        message = _normalize_message(
            _message_key(body.message_id), document.payload if document is not None else None
        )
        if message is None:
            inbox_message_events.unsubscribe(body.client_id)
            raise HTTPException(status_code=404, detail="Inbox message not found")
        inbox_message_events.subscribe(body.client_id, body.message_id)
        broadcast_ui_event(
            events_contracts.UiEvent.INBOX_MESSAGE_SELECTED,
            {"message": message.model_dump()},
            request_id=request_id,
            client_id=body.client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_SELECTED]
        )

    @router.post(
        "/inbox/{message_id}/read",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def mark_inbox_message_read(
        http_request: Request,
        message_id: str,
        client_id: str = Query(min_length=1, max_length=64),
        request: InboxMarkReadRequest | None = None,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        request_id = _request_correlation_id(http_request, x_request_id)
        read = request.read if request is not None else True
        message = await asyncio.to_thread(
            mutate_message,
            message_id,
            {"read": read, "read_at": datetime.now(UTC).isoformat() if read else None},
            request_id=request_id,
        )
        store = get_document_store()
        unread = await asyncio.to_thread(store.inbox_unread_count)
        publish_inbox_message_change(
            store,
            message,
            broadcast_ui_event=broadcast_ui_event,
            unread=unread,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED]
        )

    @router.post(
        "/inbox/{message_id}/archive",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def archive_inbox_message(
        http_request: Request,
        message_id: str,
        client_id: str = Query(min_length=1, max_length=64),
        request: InboxArchiveRequest | None = None,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        request_id = _request_correlation_id(http_request, x_request_id)
        archived = request.archived if request is not None else True
        message = await asyncio.to_thread(
            mutate_message, message_id, {"archived": archived}, request_id=request_id
        )
        store = get_document_store()
        unread = await asyncio.to_thread(store.inbox_unread_count)
        publish_inbox_message_change(
            store,
            message,
            broadcast_ui_event=broadcast_ui_event,
            unread=unread,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED]
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
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        request_id = _request_correlation_id(http_request, x_request_id)
        message = await asyncio.to_thread(
            mutate_message,
            message_id,
            {
                "decision": request.decision,
                "read": True,
                "read_at": datetime.now(UTC).isoformat(),
            },
            request_id=request_id,
        )
        store = get_document_store()
        unread = await asyncio.to_thread(store.inbox_unread_count)
        publish_inbox_message_change(
            store,
            message,
            broadcast_ui_event=broadcast_ui_event,
            unread=unread,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_UPSERTED]
        )

    @router.delete(
        "/inbox/{message_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_inbox_message(
        message_id: str,
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        _require_stream(client_id)
        request_id = _request_correlation_id(request, x_request_id)
        deleted = await asyncio.to_thread(delete_message, message_id, request_id=request_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Inbox message not found")
        store = get_document_store()
        unread = await asyncio.to_thread(store.inbox_unread_count)
        publish_inbox_message_deleted(
            store,
            message_id,
            broadcast_ui_event=broadcast_ui_event,
            unread=unread,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INBOX_MESSAGE_DELETED]
        )

    return router
