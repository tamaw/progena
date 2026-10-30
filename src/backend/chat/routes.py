"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.chat.responses as chat_responses
import src.backend.chat.service as chat_service
import src.backend.checkpoint_state as checkpoint_state
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.runtime_liveness as runtime_liveness
import src.backend.storage.documents as storage_documents
from src.backend.chat.service import (
    CHAT_MESSAGE_PAGE_SIZE,
    ChatInterruption,
    ChatMessage,
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
from src.backend.events import contracts as events_contracts
from src.backend.http.requests import BroadcastUiEvent
from src.backend.http.requests import request_correlation_id as _strict_request_correlation_id
from src.backend.integrations.runtime_history import RuntimeUnavailable
from src.backend.storage.documents import BackendDocumentStore
from src.progena.documents.kinds import SESSION_KEY_PREFIX

logger = logging.getLogger(__name__)

RUNTIME_UNAVAILABLE_DETAIL = "Progena runtime is unavailable; chat history cannot be loaded"
_CHAT_ROUTE_ORIGIN_PREFIX = "chat-route:"


def _request_id_from_chat_route_origin(origin: str | None) -> str | None:
    if origin is None or not origin.startswith(_CHAT_ROUTE_ORIGIN_PREFIX):
        return None
    request_id = origin.removeprefix(_CHAT_ROUTE_ORIGIN_PREFIX)
    return request_id or None


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
        events_contracts.UiEvent.CHAT_SESSIONS_SNAPSHOT,
        response.model_dump(),
        scope="chat",
        request_id=request_id,
    )
    return response


def create_chat_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    get_active_response: (
        Callable[[str], tuple[str, Literal["delegating", "generating"]] | None] | None
    ) = None,
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

    async def interruption_for(
        thread_id: str, messages: list[ChatMessage]
    ) -> ChatInterruption | None:
        """Read a checkpoint only when the saved transcript ends mid-turn."""
        if not messages:
            return None
        last = messages[-1]
        if last.role == "assistant" and not last.tool_calls:
            return None
        try:
            state = await checkpoint_state.read_thread_checkpoint_state(
                thread_id, get_document_store()
            )
            return _interruption_from_checkpoint_state(
                state, runtime_liveness.live_runtime(get_document_store())
            )
        except Exception:
            return None

    @router.get("/chats", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
    async def get_chats(
        request: Request,
        include_archived: bool = False,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Acknowledge chat list requests and publish the list on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        await broadcast_snapshot(include_archived=include_archived, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_SESSIONS_SNAPSHOT]
        )

    @router.get(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Acknowledge chat detail requests and publish the detail on the UI stream."""
        request_id = request_correlation_id(request, x_request_id)
        detail = await off_loop(load_detail, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        active_response = get_active_response(thread_id) if get_active_response else None
        active_request_id, activity = active_response or (None, None)
        interruption = (
            None if active_response else await interruption_for(thread_id, detail.messages)
        )
        page, has_more = paginate_chat_messages(detail.messages, limit=CHAT_MESSAGE_PAGE_SIZE)
        detail = detail.model_copy(
            update={
                "messages": page,
                "has_more_messages": has_more,
                "active_request_id": active_request_id,
                "activity": activity,
                "interruption": interruption,
            }
        )
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_SESSION_UPSERTED,
            {"session": detail.model_dump()},
            scope="chat",
            request_id=request_id,
        )
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_SESSION_SELECTED,
            {"session": detail.model_dump()},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id,
            [
                events_contracts.UiEvent.CHAT_SESSION_UPSERTED,
                events_contracts.UiEvent.CHAT_SESSION_SELECTED,
            ],
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
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
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
            events_contracts.UiEvent.CHAT_MESSAGES_PAGE_LOADED,
            response.model_dump(),
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_MESSAGES_PAGE_LOADED]
        )

    @router.patch(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def patch_chat(
        thread_id: str,
        request: ChatPatchRequest,
        http_request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Update session document metadata (title/pinned/archived) via atomic merge."""
        request_id = request_correlation_id(http_request, x_request_id)

        def _patch() -> chat_service.ChatSessionPatchResult | None:
            with storage_documents.document_write_origin(
                f"{_CHAT_ROUTE_ORIGIN_PREFIX}{request_id}"
            ):
                return patch_session_document(get_document_store(), thread_id, request)

        # Only the session document is touched, so a rename works while the
        # runtime (and with it the transcript) is unavailable.
        patched = await off_loop(_patch)
        if patched is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        if not patched.changed:
            await broadcast_snapshot(request_id=request_id)
            return http_contracts.acknowledgement_response(
                request_id, [events_contracts.UiEvent.CHAT_SESSIONS_SNAPSHOT]
            )
        return http_contracts.acknowledgement_response(
            request_id,
            [
                events_contracts.UiEvent.CHAT_SESSION_METADATA_UPDATED,
                events_contracts.UiEvent.CHAT_SESSIONS_SNAPSHOT,
            ],
        )

    @router.delete(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_chat(
        thread_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Delete the session document only; checkpoints are left intact."""
        request_id = request_correlation_id(request, x_request_id)

        def _delete() -> bool:
            store = get_document_store()
            with storage_documents.document_write_origin(
                f"{_CHAT_ROUTE_ORIGIN_PREFIX}{request_id}"
            ):
                return delete_session_document(store, thread_id)

        if not await off_loop(_delete):
            raise HTTPException(status_code=404, detail="Chat session not found")
        return http_contracts.acknowledgement_response(
            request_id,
            [
                events_contracts.UiEvent.CHAT_SESSION_DELETED,
                events_contracts.UiEvent.CHAT_SESSIONS_SNAPSHOT,
            ],
        )

    return router


def _interruption_from_checkpoint_state(
    state: dict[str, Any], live: runtime_liveness.LiveRuntime | None
) -> ChatInterruption | None:
    """Map a durable checkpoint state into the browser detail contract."""
    next_nodes = state.get("next")
    updated_at = state.get("updated_at")
    owner = state.get("runtime_instance")
    owner_instance = owner if isinstance(owner, str) else None
    if (
        state.get("exists") is not True
        or not isinstance(next_nodes, list)
        or not all(isinstance(node, str) for node in next_nodes)
        or not next_nodes
        or state.get("waiting") is True
        or state.get("failed") is True
        or not runtime_liveness.owner_is_gone(
            owner_instance, updated_at if isinstance(updated_at, str) else None, live
        )
    ):
        return None
    return ChatInterruption(next=next_nodes, at=updated_at if isinstance(updated_at, str) else None)


async def _publish_chat_session_document_event(typed: events_documents.TypedDocumentEvent) -> None:
    """Broadcast sidebar updates when a ``session:`` document changes."""

    event = typed.event
    if not event.key.startswith(SESSION_KEY_PREFIX):
        return
    request_id = _request_id_from_chat_route_origin(event.origin)
    thread_id = event.key[len(SESSION_KEY_PREFIX) :]
    if event.op == "deleted":
        await _broadcast_chat_sessions_snapshot(request_id=request_id)
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_SESSION_DELETED,
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        return
    await _broadcast_chat_sessions_snapshot(request_id=request_id)
    summary = await asyncio.to_thread(
        chat_service.get_chat_summary, storage_documents.get_default_document_store(), thread_id
    )
    if summary is None:
        return
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_SESSION_CREATED
        if event.op == "created"
        else events_contracts.UiEvent.CHAT_SESSION_METADATA_UPDATED,
        {"session": summary.model_dump()},
        scope="chat",
        session_id=thread_id,
        request_id=request_id,
    )


async def _broadcast_chat_sessions_snapshot(
    include_archived: bool = True, request_id: str | None = None
):
    return await broadcast_chat_sessions_snapshot(
        get_document_store=storage_documents.get_default_document_store,
        broadcast_ui_event=events_stream.broadcast_ui_event,
        include_archived=include_archived,
        request_id=request_id,
    )


def _get_active_response_for_thread(
    thread_id: str,
) -> tuple[str, Literal["delegating", "generating"]] | None:
    # Deferred import: `main` imports this module, so importing `main` at
    # module load time here would be circular.
    active_response = chat_responses._active_response_for_thread(thread_id)
    return active_response
