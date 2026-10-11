"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .chat_sessions import (
    CHAT_MESSAGE_PAGE_SIZE,
    ChatCreateRequest,
    ChatDraftRequest,
    ChatMessagesPageResponse,
    ChatPatchRequest,
    ChatSessionDetail,
    ChatSessionsResponse,
    UiRequestAcknowledgement,
    acknowledgement_response,
    acquire_reusable_draft,
    create_chat_metadata,
    current_state_chat_detail,
    delete_chat_metadata,
    get_chat_detail,
    is_chat_deleted,
    list_chat_sessions,
    materialize_chat_metadata,
    paginate_chat_messages,
    patch_chat_metadata,
)
from .document_store import BackendDocumentStore
from .request_utils import (
    BroadcastUiEvent,
)
from .request_utils import (
    request_correlation_id as _strict_request_correlation_id,
)
from .runtime_conversations import RuntimeUnavailable

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
    delete_conversation: Callable[[str], Awaitable[bool]] | None = None,
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

    def current_state_detail(thread_id: str) -> ChatSessionDetail | None:
        """The live (not-yet-persisted) current-session card, if it matches the id."""
        if get_current_state is None:
            return None
        try:
            state = get_current_state(thread_id) or {}
        except Exception:  # nosec B110 - state is best-effort; absence just means no card
            return None
        if not state:
            return None
        messages = get_thread_messages(thread_id) if get_thread_messages is not None else []
        return current_state_chat_detail({**state, "messages": messages})

    def detail_reasoning_score(detail: ChatSessionDetail | None) -> tuple[int, int]:
        if detail is None:
            return (0, 0)
        populated = [
            message
            for message in detail.messages
            if isinstance(message.reasoning_text, str) and message.reasoning_text.strip()
        ]
        return (len(populated), sum(len(message.reasoning_text.strip()) for message in populated))

    def prefer_live_detail(
        persisted: ChatSessionDetail | None,
        live: ChatSessionDetail | None,
    ) -> ChatSessionDetail | None:
        if live is None:
            return persisted
        if persisted is None or live.thread_id != persisted.thread_id:
            return live
        if detail_reasoning_score(live) > detail_reasoning_score(persisted):
            return live
        if live.message_count > persisted.message_count:
            return live
        return persisted

    def load_detail(thread_id: str) -> ChatSessionDetail | None:
        """Persisted or live detail; a chat the user deleted stays deleted."""
        store = get_document_store()
        if is_chat_deleted(store, thread_id):
            return None
        return prefer_live_detail(
            get_chat_detail(store, thread_id), current_state_detail(thread_id)
        )

    @router.get("/chats", status_code=202, response_model=UiRequestAcknowledgement)
    async def get_chats(
        request: Request,
        include_archived: bool = False,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge chat list requests and publish the list on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        await broadcast_snapshot(include_archived=include_archived, request_id=request_id)
        return acknowledgement_response(request_id, ["chat.sessions.snapshot"])

    @router.post("/chats", status_code=202, response_model=UiRequestAcknowledgement)
    async def create_chat(
        request: ChatCreateRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Create backend-owned chat UI metadata without duplicating transcripts."""
        request_id = request_correlation_id(http_request, x_request_id)

        def _create() -> ChatSessionDetail | None:
            store = get_document_store()
            metadata = create_chat_metadata(store, request)
            return get_chat_detail(store, metadata["thread_id"])

        try:
            detail = await off_loop(_create)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if detail is None:
            raise HTTPException(status_code=500, detail="Created chat metadata could not be loaded")
        broadcast_ui_event(
            "chat.session.created",
            {"session": detail.model_dump()},
            scope="chat",
            request_id=request_id,
        )
        await broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(
            request_id, ["chat.session.created", "chat.sessions.snapshot"]
        )

    @router.post("/chats/draft", status_code=202, response_model=UiRequestAcknowledgement)
    async def acquire_draft_chat(
        request: ChatDraftRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Reuse the newest empty draft chat or create a new one."""
        request_id = request_correlation_id(http_request, x_request_id)
        try:
            detail, created = await off_loop(acquire_reusable_draft, get_document_store(), request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        event_type = "chat.session.created" if created else "chat.session.metadata.updated"
        broadcast_ui_event(
            event_type, {"session": detail.model_dump()}, scope="chat", request_id=request_id
        )
        await broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(request_id, [event_type, "chat.sessions.snapshot"])

    @router.get("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
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
        return acknowledgement_response(
            request_id, ["chat.session.upserted", "chat.session.selected"]
        )

    @router.get(
        "/chats/{thread_id}/messages", status_code=202, response_model=UiRequestAcknowledgement
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
        return acknowledgement_response(request_id, ["chat.messages.page.loaded"])

    @router.patch("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
    async def patch_chat(
        thread_id: str,
        request: ChatPatchRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Update backend-owned chat UI metadata."""
        request_id = request_correlation_id(http_request, x_request_id)

        def _patch() -> ChatSessionDetail | None:
            store = get_document_store()
            metadata = patch_chat_metadata(store, thread_id, request)
            if metadata is None:
                detail_before_patch = get_chat_detail(store, thread_id) or current_state_detail(
                    thread_id
                )
                if detail_before_patch is not None:
                    materialize_chat_metadata(store, detail_before_patch)
                    metadata = patch_chat_metadata(store, thread_id, request)
            if metadata is None:
                return None
            return get_chat_detail(store, thread_id)

        detail = await off_loop(_patch)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        broadcast_ui_event(
            "chat.session.metadata.updated",
            {"session": detail.model_dump()},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        await broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(
            request_id, ["chat.session.metadata.updated", "chat.sessions.snapshot"]
        )

    @router.delete("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
    async def delete_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Tombstone the chat's backend metadata and delete its runtime conversation."""
        request_id = request_correlation_id(request, x_request_id)

        def _delete() -> bool:
            store = get_document_store()
            if delete_chat_metadata(store, thread_id):
                return True
            detail = get_chat_detail(store, thread_id) or current_state_detail(thread_id)
            if detail is None:
                return False
            materialize_chat_metadata(store, detail)
            return delete_chat_metadata(store, thread_id)

        if not await off_loop(_delete):
            raise HTTPException(status_code=404, detail="Chat session not found")
        # The tombstone already hides the chat; the runtime delete is best-effort
        # (the runtime may be down, or refuse with 409 while a turn runs).
        if delete_conversation is not None and not await delete_conversation(thread_id):
            logger.warning("Runtime conversation %s was not deleted", thread_id)
        broadcast_ui_event(
            "chat.session.deleted",
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        await broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(
            request_id, ["chat.session.deleted", "chat.sessions.snapshot"]
        )

    return router
