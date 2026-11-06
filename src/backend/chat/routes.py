"""Chat session routes for the backend-owned UI read model."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import ValidationError

import src.backend.chat.background_tasks as chat_background_tasks
import src.backend.chat.responses as chat_responses
import src.backend.chat.service as chat_service
import src.backend.checkpoint_state as checkpoint_state
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.lifecycle.state as lifecycle_state
import src.backend.runtime_liveness as runtime_liveness
import src.backend.schedules.service as schedules_service
import src.backend.storage.documents as storage_documents
from src.backend.chat.service import (
    CHAT_LIST_MAX_PAGE_SIZE,
    CHAT_LIST_PAGE_SIZE,
    CHAT_MESSAGE_PAGE_SIZE,
    ChatInterruption,
    ChatListFilter,
    ChatListGroupStarts,
    ChatListSort,
    ChatListView,
    ChatMessage,
    ChatMessagesPageResponse,
    ChatPatchRequest,
    ChatSessionDetail,
    delete_session_document,
    get_chat_detail,
    get_chat_list_row,
    paginate_chat_messages,
    patch_session_document,
    query_chat_list,
    summary_markers_for_page,
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


def _client_id_from_chat_route_origin(origin: str | None) -> str | None:
    if origin is None or not origin.startswith(_CHAT_ROUTE_ORIGIN_PREFIX):
        return None
    _request_id, separator, client_id = origin.removeprefix(_CHAT_ROUTE_ORIGIN_PREFIX).rpartition(
        ":"
    )
    return client_id if separator and client_id else None


def request_correlation_id(request: Request, header_request_id: str | None) -> str:
    """Resolve the middleware ID, explicitly falling back for standalone chat use."""
    try:
        return _strict_request_correlation_id(request, header_request_id)
    except RuntimeError:
        return str(uuid.uuid4())


def _require_stream(client_id: str) -> None:
    if client_id not in events_stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="Chat mutations require an active UI stream")


def create_chat_router(
    *,
    get_document_store: Callable[[], BackendDocumentStore],
    broadcast_ui_event: BroadcastUiEvent,
    get_active_response: (
        Callable[[str], tuple[str, Literal["delegating", "generating"]] | None] | None
    ) = None,
    get_runtime_client: Callable[[], Any] = lambda: lifecycle_state.client,
) -> APIRouter:
    router = APIRouter()

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

    async def queue_snapshot(thread_id: str) -> dict[str, Any] | None:
        """The runtime-owned queue, or ``None`` so the page falls back to its own request."""
        client = get_runtime_client()
        if client is None:
            return None
        try:
            return await client.get_chat_queue(thread_id)
        except Exception:
            logger.debug("Chat queue unavailable for %s", thread_id, exc_info=True)
            return None

    @router.get("/chats", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
    async def get_chats(
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        q: str = Query(default="", max_length=200),
        sort: ChatListSort = "recent",
        list_filter: ChatListFilter = Query(default="active", alias="filter"),
        source: str | None = None,
        thread_id: list[str] = Query(default=[]),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=CHAT_LIST_PAGE_SIZE, ge=0, le=CHAT_LIST_MAX_PAGE_SIZE),
        today: datetime | None = None,
        yesterday: datetime | None = None,
        this_week: datetime | None = None,
        last_week: datetime | None = None,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Publish rows ``offset`` onward of the conversation list to the requesting tab.

        The four time boundaries are the browser's local group starts; given
        together, the recent active view also counts every chat per group.
        """
        chat_background_tasks.require_stream(client_id)
        request_id = request_correlation_id(request, x_request_id)
        starts = (today, yesterday, this_week, last_week)
        if any(start is None for start in starts) and any(start is not None for start in starts):
            raise HTTPException(
                status_code=400, detail="Give all four chat list group starts or none"
            )
        try:
            view = ChatListView(
                q=q,
                sort=sort,
                filter=list_filter,
                source=source,
                thread_ids=thread_id,
                group_starts=(
                    ChatListGroupStarts(
                        today=today, yesterday=yesterday, this_week=this_week, last_week=last_week
                    )
                    if today and yesterday and this_week and last_week
                    else None
                ),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail="Invalid chat list group starts") from exc
        page = await asyncio.to_thread(
            query_chat_list, get_document_store(), view, offset=offset, limit=limit
        )
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_LIST_LOADED,
            page.model_dump(mode="json"),
            scope="chat",
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_LIST_LOADED]
        )

    @router.get(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_chat(
        thread_id: str,
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Publish the open chat, its queue and background tasks to the requesting tab."""
        chat_background_tasks.require_stream(client_id)
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
                "summary_markers": summary_markers_for_page(
                    detail.summary_markers, page, includes_transcript_head=not has_more
                ),
                "has_more_messages": has_more,
                "active_request_id": active_request_id,
                "activity": activity,
                "interruption": interruption,
            }
        )
        # Opening a chat is this tab's background-task interest in it; the
        # subscription route stays for releasing it and for reconnects.
        task_events = chat_background_tasks.background_task_events
        task_events.subscribe(client_id, thread_id)
        tasks, revision = await task_events.snapshot(get_document_store(), thread_id)
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_SESSION_SELECTED,
            {
                "session": detail.model_dump(),
                "queue": await queue_snapshot(thread_id),
                "background_tasks": {"thread_id": thread_id, "tasks": tasks, "revision": revision},
            },
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_SESSION_SELECTED]
        )

    @router.get(
        "/chats/{thread_id}/messages",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_chat_messages(
        thread_id: str,
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        before_message_id: str | None = None,
        limit: int = CHAT_MESSAGE_PAGE_SIZE,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Acknowledge an older-messages page request and publish it on the UI stream."""
        chat_background_tasks.require_stream(client_id)
        request_id = request_correlation_id(request, x_request_id)
        detail = await off_loop(load_detail, thread_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        page, has_more = paginate_chat_messages(
            detail.messages, limit=limit, before_message_id=before_message_id
        )
        response = ChatMessagesPageResponse(
            thread_id=thread_id,
            messages=page,
            summary_markers=summary_markers_for_page(
                detail.summary_markers, page, includes_transcript_head=not has_more
            ),
            has_more=has_more,
        )
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_MESSAGES_PAGE_LOADED,
            response.model_dump(),
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
            client_id=client_id,
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
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Update session document metadata (title/pinned/archived) via atomic merge."""
        _require_stream(client_id)
        request_id = request_correlation_id(http_request, x_request_id)

        def _patch() -> chat_service.ChatSessionPatchResult | None:
            with storage_documents.document_write_origin(
                f"{_CHAT_ROUTE_ORIGIN_PREFIX}{request_id}:{client_id}"
            ):
                return patch_session_document(get_document_store(), thread_id, request)

        # Only the session document is touched, so a rename works while the
        # runtime (and with it the transcript) is unavailable.
        patched = await off_loop(_patch)
        if patched is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        row = await off_loop(get_chat_list_row, get_document_store(), thread_id)
        if row is not None:
            broadcast_ui_event(
                events_contracts.UiEvent.CHAT_LIST_ROW_UPSERTED,
                {"row": row.model_dump()},
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
                client_id=client_id,
            )
        if request.archived:
            # Archiving stops the chat's schedules; unarchiving never resumes them.
            schedules_service.pause_conversation_schedules(thread_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_LIST_ROW_UPSERTED]
        )

    @router.delete(
        "/chats/{thread_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_chat(
        thread_id: str,
        request: Request,
        client_id: str = Query(min_length=1, max_length=64),
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ):
        """Delete the session document only; checkpoints are left intact."""
        _require_stream(client_id)
        request_id = request_correlation_id(request, x_request_id)

        def _delete() -> bool:
            store = get_document_store()
            with storage_documents.document_write_origin(
                f"{_CHAT_ROUTE_ORIGIN_PREFIX}{request_id}:{client_id}"
            ):
                return delete_session_document(store, thread_id)

        if not await off_loop(_delete):
            raise HTTPException(status_code=404, detail="Chat session not found")
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_LIST_ROW_DELETED,
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
            client_id=client_id,
        )
        schedules_service.delete_conversation_schedules(thread_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.CHAT_LIST_ROW_DELETED]
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


# Last row sent per chat. A session write reaches the list twice (its document
# event, and the store notification that also covers every-turn writes); the
# second, uncorrelated copy of an unchanged row is not resent.
_sent_chat_list_rows: dict[str, dict[str, Any]] = {}


async def publish_chat_list_row(thread_id: str, *, exclude_client_id: str | None = None) -> None:
    """Send a session's current list row to every tab watching Chats."""
    row = await asyncio.to_thread(
        chat_service.get_chat_list_row, storage_documents.get_default_document_store(), thread_id
    )
    if row is None:
        return
    payload = row.model_dump()
    if _sent_chat_list_rows.get(thread_id) == payload:
        return
    _sent_chat_list_rows[thread_id] = payload
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_LIST_ROW_UPSERTED,
        {"row": payload},
        scope="chat",
        session_id=thread_id,
        topic="chats",
        exclude_client_id=exclude_client_id,
    )


async def _publish_chat_session_document_event(typed: events_documents.TypedDocumentEvent) -> None:
    """Turn ``session:`` document changes into conversation-list row events."""

    event = typed.event
    if not event.key.startswith(SESSION_KEY_PREFIX):
        return
    client_id = _client_id_from_chat_route_origin(event.origin)
    thread_id = event.key[len(SESSION_KEY_PREFIX) :]
    if event.op == "deleted":
        _sent_chat_list_rows.pop(thread_id, None)
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_LIST_ROW_DELETED,
            {"thread_id": thread_id},
            scope="chat",
            session_id=thread_id,
            topic="chats",
            exclude_client_id=client_id,
        )
        return
    # The event is historical; the row sent is the document as it is now.
    await publish_chat_list_row(thread_id, exclude_client_id=client_id)


def _get_active_response_for_thread(
    thread_id: str,
) -> tuple[str, Literal["delegating", "generating"]] | None:
    # Deferred import: `main` imports this module, so importing `main` at
    # module load time here would be circular.
    active_response = chat_responses._active_response_for_thread(thread_id)
    return active_response
