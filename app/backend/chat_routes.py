"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from .chat_sessions import (
    ChatCreateRequest,
    ChatDraftRequest,
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
    list_chat_sessions,
    materialize_chat_metadata,
    patch_chat_metadata,
)
from .document_store import BackendDocumentStore
from .request_utils import (
    BroadcastUiEvent,
)
from .request_utils import (
    request_correlation_id as _strict_request_correlation_id,
)


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


def broadcast_chat_sessions_snapshot(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    include_archived: bool = True,
    request_id: str | None = None,
) -> ChatSessionsResponse:
    response = chat_sessions_response(
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
    get_current_state: Callable[[], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()

    def broadcast_snapshot(
        include_archived: bool = True, request_id: str | None = None
    ) -> ChatSessionsResponse:
        return broadcast_chat_sessions_snapshot(
            get_document_store=get_document_store,
            broadcast_ui_event=broadcast_ui_event,
            include_archived=include_archived,
            request_id=request_id,
        )

    def current_state_detail(thread_id: str) -> ChatSessionDetail | None:
        """The live (not-yet-persisted) current-session card, if it matches the id."""
        if get_current_state is None:
            return None
        try:
            state = get_current_state() or {}
        except Exception:  # nosec B110 - state is best-effort; absence just means no card
            return None
        detail = current_state_chat_detail(state)
        if detail is not None and detail.thread_id == thread_id:
            return detail
        return None

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

    @router.get("/chats", status_code=202, response_model=UiRequestAcknowledgement)
    async def get_chats(
        request: Request,
        include_archived: bool = False,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge chat list requests and publish the list on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        broadcast_snapshot(
            include_archived=include_archived,
            request_id=request_id,
        )
        return acknowledgement_response(request_id, ["chat.sessions.snapshot"])

    @router.post("/chats", status_code=202, response_model=UiRequestAcknowledgement)
    async def create_chat(
        request: ChatCreateRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Create backend-owned chat UI metadata without duplicating transcripts."""
        request_id = request_correlation_id(http_request, x_request_id)
        store = get_document_store()
        try:
            metadata = create_chat_metadata(store, request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        detail = get_chat_detail(store, metadata["thread_id"])
        if detail is None:
            raise HTTPException(status_code=500, detail="Created chat metadata could not be loaded")
        broadcast_ui_event(
            "chat.session.created",
            {"session": detail.model_dump()},
            scope="chat",
            request_id=request_id,
        )
        broadcast_snapshot(request_id=request_id)
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
        store = get_document_store()
        try:
            detail, created = acquire_reusable_draft(store, request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        event_type = "chat.session.created" if created else "chat.session.metadata.updated"
        broadcast_ui_event(
            event_type, {"session": detail.model_dump()}, scope="chat", request_id=request_id
        )
        broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(request_id, [event_type, "chat.sessions.snapshot"])

    @router.get("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
    async def get_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Acknowledge chat detail requests and publish the detail on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        detail = prefer_live_detail(
            get_chat_detail(get_document_store(), thread_id),
            current_state_detail(thread_id),
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
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

    @router.patch("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
    async def patch_chat(
        thread_id: str,
        request: ChatPatchRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Update backend-owned chat UI metadata."""
        request_id = request_correlation_id(http_request, x_request_id)
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
            raise HTTPException(status_code=404, detail="Chat session not found")
        detail = get_chat_detail(store, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        broadcast_ui_event(
            "chat.session.metadata.updated",
            {"session": detail.model_dump()},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(
            request_id, ["chat.session.metadata.updated", "chat.sessions.snapshot"]
        )

    @router.delete("/chats/{thread_id}", status_code=202, response_model=UiRequestAcknowledgement)
    async def delete_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        """Soft-delete backend chat UI metadata."""
        request_id = request_correlation_id(request, x_request_id)
        store = get_document_store()
        deleted = delete_chat_metadata(store, thread_id)
        if not deleted:
            detail = get_chat_detail(store, thread_id) or current_state_detail(thread_id)
            if detail is not None:
                materialize_chat_metadata(store, detail)
                deleted = delete_chat_metadata(store, thread_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat session not found")
        broadcast_ui_event(
            "chat.session.deleted",
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        broadcast_snapshot(request_id=request_id)
        return acknowledgement_response(
            request_id, ["chat.session.deleted", "chat.sessions.snapshot"]
        )

    return router
