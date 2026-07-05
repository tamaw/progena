"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from fastapi import APIRouter, HTTPException

from .chat_sessions import (
    ChatCreateRequest,
    ChatDraftRequest,
    ChatMutationResponse,
    ChatPatchRequest,
    ChatSessionDetail,
    ChatSessionsResponse,
    UiEventEnvelope,
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


class BroadcastUiEvent(Protocol):
    def __call__(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        scope: Literal["global", "session", "chat"] = "global",
        session_id: str | None = None,
    ) -> UiEventEnvelope: ...


def chat_sessions_response(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    include_archived: bool = False,
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
) -> ChatSessionsResponse:
    response = chat_sessions_response(
        get_document_store=get_document_store,
        include_archived=include_archived,
    )
    broadcast_ui_event(
        "chat.sessions.snapshot",
        response.model_dump(),
        scope="chat",
    )
    return response


def create_chat_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    get_current_state: Callable[[], dict[str, Any]] | None = None,
) -> APIRouter:
    router = APIRouter()

    def broadcast_snapshot(include_archived: bool = True) -> ChatSessionsResponse:
        return broadcast_chat_sessions_snapshot(
            get_document_store=get_document_store,
            broadcast_ui_event=broadcast_ui_event,
            include_archived=include_archived,
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

    @router.get("/chats", response_model=ChatSessionsResponse)
    async def get_chats(include_archived: bool = False):
        """Acknowledge chat list requests and publish the list on the UI stream."""
        response = chat_sessions_response(
            get_document_store=get_document_store,
            include_archived=include_archived,
        )
        broadcast_snapshot()
        return response

    @router.post("/chats", response_model=ChatMutationResponse)
    async def create_chat(request: ChatCreateRequest):
        """Create backend-owned chat UI metadata without duplicating transcripts."""
        store = get_document_store()
        try:
            metadata = create_chat_metadata(store, request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        detail = get_chat_detail(store, metadata["thread_id"])
        if detail is None:
            raise HTTPException(status_code=500, detail="Created chat metadata could not be loaded")
        broadcast_ui_event("chat.session.created", {"session": detail.model_dump()}, scope="chat")
        broadcast_snapshot()
        return ChatMutationResponse(session=detail)

    @router.post("/chats/draft", response_model=ChatMutationResponse)
    async def acquire_draft_chat(request: ChatDraftRequest):
        """Reuse the newest empty draft chat or create a new one."""
        store = get_document_store()
        try:
            detail, created = acquire_reusable_draft(store, request)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        event_type = "chat.session.created" if created else "chat.session.metadata.updated"
        broadcast_ui_event(event_type, {"session": detail.model_dump()}, scope="chat")
        broadcast_snapshot()
        return ChatMutationResponse(session=detail)

    @router.get("/chats/{thread_id}", response_model=ChatSessionDetail)
    async def get_chat(thread_id: str, request_id: str | None = None):
        """Acknowledge chat detail requests and publish the detail on the UI stream."""
        detail = prefer_live_detail(
            get_chat_detail(get_document_store(), thread_id),
            current_state_detail(thread_id),
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        broadcast_ui_event("chat.session.upserted", {"session": detail.model_dump()}, scope="chat")
        broadcast_ui_event(
            "chat.session.selected",
            {"session": detail.model_dump(), "request_id": request_id},
            scope="chat",
            session_id=thread_id,
        )
        return detail

    @router.patch("/chats/{thread_id}", response_model=ChatMutationResponse)
    async def patch_chat(thread_id: str, request: ChatPatchRequest):
        """Update backend-owned chat UI metadata."""
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
        )
        broadcast_snapshot()
        return ChatMutationResponse(session=detail)

    @router.delete("/chats/{thread_id}", status_code=204)
    async def delete_chat(thread_id: str):
        """Soft-delete backend chat UI metadata."""
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
        )
        broadcast_snapshot()
        return None

    return router
