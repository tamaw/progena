"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.chat.responses as chat_responses
import src.backend.chat.service as chat_service
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.storage.documents as storage_documents
from src.backend.chat.service import (
    CHAT_MESSAGE_PAGE_SIZE,
    ChatMessagesPageResponse,
    ChatPatchRequest,
    ChatSessionDetail,
    ChatSessionsResponse,
    delete_session_document,
    get_chat_detail,
    list_chat_sessions,
    paginate_chat_messages,
    patch_session_document,
)
from src.backend.http.requests import BroadcastUiEvent
from src.backend.http.requests import request_correlation_id as _strict_request_correlation_id
from src.backend.integrations.runtime_history import RuntimeUnavailable
from src.backend.storage.documents import BackendDocumentStore
from src.progenitor.documents.kinds import SESSION_KEY_PREFIX

logger = logging.getLogger(__name__)

RUNTIME_UNAVAILABLE_DETAIL = "Progenitor runtime is unavailable; chat history cannot be loaded"


def request_correlation_id(request: Request, header_request_id: str | None) -> str:
    """Resolve the middleware ID, explicitly falling back for standalone chat use."""
    try:
        return _strict_request_correlation_id(request, header_request_id)
    except RuntimeError:
        return str(uuid.uuid4())


def chat_sessions_response(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    include_archived: bool = False,
    request_id: str | None = None,
) -> ChatSessionsResponse:
    sessions = list_chat_sessions(
        get_document_store(),
        include_archived=include_archived,
    )
    return ChatSessionsResponse(sessions=sessions, total=len(sessions))


async def broadcast_chat_sessions_snapshot(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    include_archived: bool = True,
    request_id: str | None = None,
) -> ChatSessionsResponse:
    # Building the list reads Postgres and the runtime; keep that off the event
    # loop, and broadcast from the loop itself.
    response = await asyncio.to_thread(
        chat_sessions_response,
        get_document_store=get_document_store,
        include_archived=include_archived,
    )
    broadcast_ui_event(
        "chat.sessions.snapshot",
        response.model_dump(),
        scope="chat",
        request_id=request_id,
    )
    return response


def create_chat_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    get_current_state: Callable[[str], dict[str, Any]] | None = None,
    get_thread_messages: Callable[[str], list[Any]] | None = None,
    get_active_request_id: Callable[[str], str | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    async def broadcast_snapshot(
        include_archived: bool = True, request_id: str | None = None
    ) -> ChatSessionsResponse:
        return await broadcast_chat_sessions_snapshot(
            get_document_store=get_document_store,
            broadcast_ui_event=broadcast_ui_event,
            include_archived=include_archived,
            request_id=request_id,
        )

    async def off_loop(fn: Callable[..., Any], *args: Any) -> Any:
        """Run blocking chat reads (Postgres, runtime) in a worker thread.

        A runtime that is down surfaces as 503 instead of an empty chat.
        """
        try:
            return await asyncio.to_thread(fn, *args)
        except RuntimeUnavailable as exc:
            logger.warning("Chat read failed: %s", exc)
            raise HTTPException(status_code=503, detail=RUNTIME_UNAVAILABLE_DETAIL) from exc

    def load_detail(thread_id: str) -> ChatSessionDetail | None:
        """Persisted detail; a chat the user deleted stays deleted."""
        return get_chat_detail(get_document_store(), thread_id)

    @router.get("/chats", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
    async def get_chats(
        request: Request,
        include_archived: bool = False,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge chat list requests and publish the list on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        await broadcast_snapshot(include_archived=include_archived, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["chat.sessions.snapshot"])

    @router.get(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge chat detail requests and publish the detail on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        detail = await off_loop(load_detail, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        page, has_more = paginate_chat_messages(detail.messages, limit=CHAT_MESSAGE_PAGE_SIZE)
        active_request_id = get_active_request_id(thread_id) if get_active_request_id else None
        detail = detail.model_copy(
            update={
                "messages": page,
                "has_more_messages": has_more,
                "active_request_id": active_request_id,
            }
        )
        broadcast_ui_event(
            "chat.session.upserted",
            {"session": detail.model_dump()},
            scope="chat",
            request_id=request_id,
        )
        broadcast_ui_event(
            "chat.session.selected",
            {"session": detail.model_dump()},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, ["chat.session.upserted", "chat.session.selected"]
        )

    @router.get(
        "/chats/{thread_id}/messages",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_chat_messages(
        thread_id: str,
        request: Request,
        before_message_id: str | None = None,
        limit: int = CHAT_MESSAGE_PAGE_SIZE,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge an older-messages page request and publish it on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        detail = await off_loop(load_detail, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        page, has_more = paginate_chat_messages(
            detail.messages, limit=limit, before_message_id=before_message_id
        )
        response = ChatMessagesPageResponse(thread_id=thread_id, messages=page, has_more=has_more)
        broadcast_ui_event(
            "chat.messages.page.loaded",
            response.model_dump(),
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(request_id, ["chat.messages.page.loaded"])

    @router.patch(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def patch_chat(
        thread_id: str,
        request: ChatPatchRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Update session document metadata (title/pinned/archived) via atomic merge."""
        request_id = request_correlation_id(http_request, x_request_id)

        def _patch() -> dict | None:
            return patch_session_document(get_document_store(), thread_id, request)

        # Only the session document is touched, so a rename works while the
        # runtime (and with it the transcript) is unavailable.
        if await off_loop(_patch) is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        # The document-event handler broadcasts the snapshot and metadata.updated
        # event so the sidebar updates live for all clients.
        return http_contracts.acknowledgement_response(
            request_id, ["chat.session.metadata.updated", "chat.sessions.snapshot"]
        )

    @router.delete(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Delete the session document only; checkpoints are left intact."""
        request_id = request_correlation_id(request, x_request_id)

        def _delete() -> bool:
            store = get_document_store()
            return delete_session_document(store, thread_id)

        if not await off_loop(_delete):
            raise HTTPException(status_code=404, detail="Chat session not found")
        # The document-event handler broadcasts the snapshot and deleted event.
        return http_contracts.acknowledgement_response(
            request_id, ["chat.session.deleted", "chat.sessions.snapshot"]
        )

    return router


async def _publish_chat_session_document_event(typed: events_documents.TypedDocumentEvent) -> None:
    """Broadcast sidebar updates when a ``session:`` document changes."""

    event = typed.event
    if not event.key.startswith(SESSION_KEY_PREFIX):
        return
    thread_id = event.key[len(SESSION_KEY_PREFIX) :]
    # Always push the refreshed roster so every client stays in sync.
    await _broadcast_chat_sessions_snapshot()
    if event.op == "deleted":
        events_stream.broadcast_ui_event(
            "chat.session.deleted",
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
        )
        return
    summary = await asyncio.to_thread(
        chat_service.get_chat_summary, storage_documents.get_default_document_store(), thread_id
    )
    if summary is None:
        return
    events_stream.broadcast_ui_event(
        "chat.session.created" if event.op == "created" else "chat.session.metadata.updated",
        {"session": summary.model_dump()},
        scope="chat",
        session_id=thread_id,
    )


async def _broadcast_chat_sessions_snapshot(include_archived: bool = True):
    return await broadcast_chat_sessions_snapshot(
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
        include_archived=include_archived,
    )


def _get_active_request_id_for_thread(thread_id: str) -> str | None:
    # Deferred import: `main` imports this module, so importing `main` at
    # module load time here would be circular.

    return chat_responses._active_request_id_for_thread(thread_id)
