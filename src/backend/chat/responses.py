"""Chat responses."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.sse import EventSourceResponse, format_sse_event

import src.backend.attachments.processing as attachments_processing
import src.backend.attachments.routes as attachments_routes
import src.backend.attachments.store as attachments_store
import src.backend.chat.models as chat_models
import src.backend.chat.session as chat_session
import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.lifecycle.state as lifecycle_state
from src.progena.sessions.titles import ATTACHMENT_CONTEXT_MARKER

router = APIRouter()

logger = logging.getLogger(__name__)

_active_response_streams: dict[str, asyncio.Event] = {}


_response_request_ids: dict[str, str] = {}


RESPONSE_STREAM_REPLAY_LIMIT = 1024


RESPONSE_STREAM_SUBSCRIBER_QUEUE_SIZE = 100


RESPONSE_STREAM_KEEPALIVE_SECONDS = 15.0


RESPONSE_STREAM_TTL_SECONDS = 120.0


@dataclass
class ResponseStreamFrame:
    """One replayable response-content frame for a browser request."""

    id: str
    seq: int
    event_type: str
    payload: dict[str, Any]


@dataclass
class ResponseStreamState:
    """In-memory response-content stream state scoped to one browser request."""

    request_id: str
    response_id: str
    thread_id: str
    model: str
    frames: list[ResponseStreamFrame]
    subscribers: dict[asyncio.Queue, asyncio.Task | None]
    next_seq: int
    terminal_reached: bool
    expiry_deadline: float | None


@dataclass
class ResponseActivityState:
    """Live presentation state for one non-terminal browser response."""

    request_id: str
    thread_id: str
    activity: Literal["delegating", "generating"] = "generating"
    active_delegation_ids: set[str] = field(default_factory=set)


_response_streams: dict[str, ResponseStreamState] = {}


_response_activities: dict[str, ResponseActivityState] = {}


_WAITING_DELEGATION_TOOL_NAMES = frozenset({"task", "a2a_send", "acp_send"})

_WAITING_STATUS_TOOL_NAMES = frozenset({"a2a_status", "acp_status"})


_TERMINAL_A2A_STATES = frozenset(
    {
        "completed",
        "failed",
        "rejected",
        "cancelled",
        "canceled",
        "input-required",
        "input_required",
    }
)


def _register_response_activity(request_id: str, thread_id: str) -> None:
    """Record a response as generating before it starts waiting on delegated work.

    ``chat.response.started`` owns the initial generating state. The activity
    event is reserved for changes between generating and delegating.
    """
    _response_activities[request_id] = ResponseActivityState(
        request_id=request_id,
        thread_id=thread_id,
    )


def _clear_response_activity(request_id: str) -> None:
    """Forget a terminal response so reloads cannot report a stale activity."""
    _response_activities.pop(request_id, None)


def _active_response_for_thread(
    thread_id: str,
) -> tuple[str, Literal["delegating", "generating"]] | None:
    """Return the newest live response for one thread from the activity registry."""
    for state in reversed(_response_activities.values()):
        if state.thread_id == thread_id:
            return state.request_id, state.activity
    return None


def _set_response_delegation_active(
    request_id: str,
    delegation_id: str,
    *,
    active: bool,
) -> None:
    """Apply one known delegated-work lifecycle edge to a response.

    The set makes concurrent task calls deterministic: only the first active
    delegation changes to ``delegating``, and only the final terminal
    delegation restores ``generating``.
    """
    state = _response_activities.get(request_id)
    if state is None:
        return

    was_delegating = bool(state.active_delegation_ids)
    if active:
        state.active_delegation_ids.add(delegation_id)
    else:
        state.active_delegation_ids.discard(delegation_id)
    is_delegating = bool(state.active_delegation_ids)
    if is_delegating == was_delegating:
        return

    state.activity = "delegating" if is_delegating else "generating"
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_RESPONSE_ACTIVITY,
        {"thread_id": state.thread_id, "activity": state.activity},
        scope="chat",
        session_id=state.thread_id,
        request_id=request_id,
    )


def _status_call_waits(metadata: dict[str, Any]) -> bool:
    """Return whether an A2A/ACP status call is explicitly long-polling."""
    args_preview = metadata.get("args_preview")
    if not isinstance(args_preview, dict):
        return False
    wait_seconds = args_preview.get("wait_seconds")
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float, str)):
        return False
    try:
        return float(wait_seconds) > 0
    except (TypeError, ValueError):
        return False


def _track_response_delegation_event(request_id: str, item: dict[str, Any]) -> None:
    """Track only runtime delegation lifecycles that make the parent wait.

    Runtime structured stream events provide the matching ``tool_call_id`` for
    parent ``task``, blocking A2A/ACP send, and explicit long-poll status calls,
    plus their custom subagent/A2A counterparts. Explicitly asynchronous launch
    events are intentionally not tracked: their parent continues after launch.
    """
    event_type = item.get("event_type")
    metadata = item.get("metadata")
    if not isinstance(event_type, str) or not isinstance(metadata, dict):
        return
    raw_delegation_id = metadata.get("tool_call_id")
    if not isinstance(raw_delegation_id, str):
        return
    delegation_id = raw_delegation_id.strip()
    if not delegation_id:
        return

    if event_type == "subagent_start":
        _set_response_delegation_active(request_id, delegation_id, active=True)
        return
    if event_type == "subagent_result":
        _set_response_delegation_active(request_id, delegation_id, active=False)
        return
    if event_type == "a2a_call_progress":
        state = metadata.get("state")
        is_terminal = isinstance(state, str) and state.strip().lower() in _TERMINAL_A2A_STATES
        _set_response_delegation_active(request_id, delegation_id, active=not is_terminal)
        return

    tool_name = metadata.get("tool_name")
    if not isinstance(tool_name, str):
        content = item.get("content")
        tool_name = content if isinstance(content, str) else ""
    if event_type == "tool_start":
        if tool_name in _WAITING_DELEGATION_TOOL_NAMES or (
            tool_name in _WAITING_STATUS_TOOL_NAMES and _status_call_waits(metadata)
        ):
            _set_response_delegation_active(request_id, delegation_id, active=True)
    elif event_type in {"tool_result", "tool_error"} and (
        tool_name in _WAITING_DELEGATION_TOOL_NAMES or tool_name in _WAITING_STATUS_TOOL_NAMES
    ):
        _set_response_delegation_active(request_id, delegation_id, active=False)


def _response_stream_frame_id(request_id: str, seq: int) -> str:
    """Return the stable replay cursor for one response-content frame."""
    return f"chat-response:{request_id}:{seq}"


def _parse_response_stream_cursor(request_id: str, cursor: str | None) -> tuple[str, int | None]:
    """Validate a browser replay cursor for its response request."""
    if cursor is None or not cursor.strip():
        return "missing", None
    prefix = f"chat-response:{request_id}:"
    if not cursor.startswith(prefix):
        return "invalid", None
    try:
        seq = int(cursor.removeprefix(prefix))
    except ValueError:
        return "invalid", None
    return ("ok", seq) if seq >= 0 else ("invalid", None)


def _ensure_response_stream(
    request_id: str, response_id: str, thread_id: str, model: str
) -> ResponseStreamState:
    """Return the request-scoped dedicated stream, creating it once."""
    state = _response_streams.get(request_id)
    if state is not None:
        return state
    state = ResponseStreamState(
        request_id=request_id,
        response_id=response_id,
        thread_id=thread_id,
        model=model,
        frames=[],
        subscribers={},
        next_seq=0,
        terminal_reached=False,
        expiry_deadline=None,
    )
    _response_streams[request_id] = state
    return state


def _notify_response_stream_subscribers(
    state: ResponseStreamState, frame: ResponseStreamFrame | None
) -> None:
    """Deliver one live frame or terminal wake-up to each subscriber queue."""
    stale_subscribers: list[asyncio.Queue] = []
    for queue in list(state.subscribers):
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            stale_subscribers.append(queue)
    for queue in stale_subscribers:
        task = state.subscribers.pop(queue, None)
        if task is not None and not task.done():
            task.cancel()
        logger.warning("Event queue full for response stream subscriber — closing stale connection")


def _append_response_stream_frame(
    state: ResponseStreamState, event_type: str, payload: dict[str, Any]
) -> ResponseStreamFrame:
    """Append one content frame, retaining it for replay and live subscribers."""
    state.next_seq += 1
    frame = ResponseStreamFrame(
        id=_response_stream_frame_id(state.request_id, state.next_seq),
        seq=state.next_seq,
        event_type=event_type,
        payload=payload,
    )
    state.frames.append(frame)
    del state.frames[:-RESPONSE_STREAM_REPLAY_LIMIT]
    _notify_response_stream_subscribers(state, frame)
    return frame


def _mark_response_stream_terminal(state: ResponseStreamState) -> None:
    """Mark a dedicated response stream terminal and wake waiting subscribers."""
    if state.terminal_reached:
        return
    state.terminal_reached = True
    state.expiry_deadline = time.monotonic() + RESPONSE_STREAM_TTL_SECONDS
    _notify_response_stream_subscribers(state, None)
    _expire_response_stream_later(state.request_id, state)


def _expire_response_stream_later(request_id: str, state: ResponseStreamState) -> None:
    """Remove a terminal response stream after its replay grace period."""
    if state.expiry_deadline is None:
        return

    async def expire() -> None:
        assert state.expiry_deadline is not None
        await asyncio.sleep(max(0.0, state.expiry_deadline - time.monotonic()))
        if _response_streams.get(request_id) is state and time.monotonic() >= state.expiry_deadline:
            _response_streams.pop(request_id, None)

    _spawn_background_task(expire())


_background_tasks: set[asyncio.Task] = set()


def _spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """``asyncio.create_task`` with a strong reference held until completion."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _request_correlation_id(request: Request, header_request_id: str | None) -> str:
    """Resolve middleware correlation, with an explicit standalone fallback."""
    try:
        return http_requests.request_correlation_id(request, header_request_id)
    except RuntimeError:
        return str(uuid.uuid4())


async def _generate_response_stream(
    state: ResponseStreamState,
    cursor: str | None,
) -> AsyncGenerator[bytes, None]:
    """Yield one request's replayable response content and live frames."""
    queue: asyncio.Queue[ResponseStreamFrame | None] = asyncio.Queue(
        maxsize=RESPONSE_STREAM_SUBSCRIBER_QUEUE_SIZE
    )
    current_task = asyncio.current_task()
    state.subscribers[queue] = current_task

    def encode_event(
        event: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> bytes:
        return cast(bytes, format_sse_event(data_str=json.dumps(payload), event=event, id=event_id))

    try:
        cursor_status, cursor_seq = _parse_response_stream_cursor(state.request_id, cursor)
        if cursor_status == "invalid":
            yield encode_event(
                events_contracts.ChatContentEvent.RESYNC_REQUIRED,
                {"request_id": state.request_id, "reason": "invalid_cursor"},
            )
            return

        retained_frames = list(state.frames)
        if cursor_status == "ok":
            assert cursor_seq is not None
            if retained_frames and cursor_seq < retained_frames[0].seq - 1:
                yield encode_event(
                    events_contracts.ChatContentEvent.RESYNC_REQUIRED,
                    {"request_id": state.request_id, "reason": "cursor_expired"},
                )
                return
        else:
            cursor_seq = 0

        for frame in retained_frames:
            if frame.seq > cursor_seq:
                yield encode_event(frame.event_type, frame.payload, frame.id)

        while True:
            if state.terminal_reached and queue.empty():
                break
            try:
                async with asyncio.timeout(RESPONSE_STREAM_KEEPALIVE_SECONDS):
                    queued_frame = await queue.get()
            except TimeoutError:
                if state.terminal_reached and queue.empty():
                    break
                yield encode_event(
                    events_contracts.ChatContentEvent.HEARTBEAT,
                    {"request_id": state.request_id, "timestamp": time.time()},
                )
                continue

            if queued_frame is None:
                if state.terminal_reached and queue.empty():
                    break
                continue
            yield encode_event(queued_frame.event_type, queued_frame.payload, queued_frame.id)
    finally:
        state.subscribers.pop(queue, None)


@router.get("/api/ui/responses/{request_id}/stream")
async def response_stream(
    request_id: str,
    after: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Serve replayable response content without loading the global UI stream."""
    if not request_id.strip():
        raise HTTPException(status_code=404, detail="Unknown response stream")

    state = _response_streams.get(request_id)
    now = time.monotonic()
    if state is None or (state.expiry_deadline is not None and now >= state.expiry_deadline):
        if state is not None and _response_streams.get(request_id) is state:
            _response_streams.pop(request_id, None)
        raise HTTPException(status_code=404, detail="Unknown response stream")

    cursor = last_event_id if last_event_id is not None else after
    return EventSourceResponse(
        _generate_response_stream(state, cursor),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/api/ui/responses", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def responses(
    request: Request,
    response: Response,
    body: chat_models.ResponseRequest,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Dispatch a browser generation and acknowledge its lifecycle on ui.event.

    Streaming response content travels on the request-scoped response SSE endpoint.
    """
    request_id = _request_correlation_id(request, x_request_id)

    try:
        if body.stream:
            chat_models.StreamingResponseRequest.validate_for_streaming(body)
        last_user_msg = _extract_response_input(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not lifecycle_state.client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    session = await chat_session.get_session_from_request(request, lifecycle_state.session_manager)
    thread_id, request_metadata = _build_chat_request_metadata(session, body.metadata)
    last_user_msg = _resolve_message_attachments(last_user_msg, request_metadata, thread_id)
    await lifecycle_state.session_manager.record_chat_activity(
        session.session_id,
        message_increment=1,
    )

    response_id = f"resp-{uuid.uuid4().hex[:8]}"
    _active_response_streams[request_id] = asyncio.Event()
    _register_response_activity(request_id, thread_id)
    if body.stream:
        _ensure_response_stream(request_id, response_id, thread_id, body.model)
    _response_request_ids[response_id] = request_id

    # Task creation is the generation dispatch. Enqueue the causal started
    # event only after that dispatch and before acknowledging it.
    _spawn_background_task(
        _run_response_event_stream(
            message=last_user_msg,
            model=body.model,
            request_id=request_id,
            response_id=response_id,
            thread_id=thread_id,
            metadata=request_metadata,
            stream=body.stream,
            include_reasoning=body.include_reasoning,
        )
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_RESPONSE_STARTED,
        {
            "id": response_id,
            "model": body.model,
            "thread_id": thread_id,
            "stream": body.stream,
        },
        scope="chat",
        session_id=thread_id,
        request_id=request_id,
    )

    _attach_session_cookie(request, session, response)

    # Only started is guaranteed synchronously for every accepted generation.
    # Terminal and chunk event types depend on the generation outcome.
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.CHAT_RESPONSE_STARTED]
    )


@router.put(
    "/api/ui/responses/{generation_id}/cancel",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def cancel_response(
    generation_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Cancel a generation using either its browser response or request ID."""
    request_id = _request_correlation_id(request, x_request_id)
    target_id = generation_id.strip()
    if not target_id:
        raise HTTPException(status_code=400, detail="generation_id is required")

    target_request_id = _response_request_ids.get(target_id, target_id)
    target_response_id = target_id if target_id in _response_request_ids else None
    cancel_event = _active_response_streams.get(target_request_id)
    if cancel_event is not None:
        cancel_event.set()

    progena_cancelled = False
    if lifecycle_state.client is not None:
        cancel_request = getattr(lifecycle_state.client, "cancel_request", None)
        if callable(cancel_request):
            result = cancel_request(target_request_id)
            progena_cancelled = bool(await result) if inspect.isawaitable(result) else bool(result)

    if cancel_event is None and not progena_cancelled:
        raise HTTPException(
            status_code=404, detail="No active response stream for target_request_id"
        )

    # This event resolves the cancellation operation. The generation runner
    # separately emits its terminal event with the target request ID.
    cancellation_payload = {
        "target_request_id": target_request_id,
        "cancel_request_id": request_id,
        "cancelled": True,
    }
    if target_response_id is not None:
        cancellation_payload["target_response_id"] = target_response_id
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.CHAT_RESPONSE_CANCEL_REQUESTED,
        cancellation_payload,
        scope="chat",
        request_id=request_id,
    )
    if cancel_event is None:
        # A generation accepted by another backend instance has no local runner
        # to emit the target terminal event after a successful remote cancel.
        _clear_response_activity(target_request_id)
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED,
            cancellation_payload,
            scope="chat",
            request_id=target_request_id,
        )

    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.CHAT_RESPONSE_CANCEL_REQUESTED]
    )


def _extract_response_input(request: chat_models.ResponseRequest) -> str:
    """Normalize Responses API input to the validated final user string.

    Empty text is allowed when the request carries attachments -- see
    `StreamingResponseRequest.validate_for_streaming`'s matching allowance
    for the streaming path. The frontend always sends `input` as a message
    list (`buildResponsesInput` in useChatApi.js), and it drops any entry
    with empty content, so an attachment-only send arrives as `input: []`
    -- not an empty string -- and must be allowed here too, not just in
    the `isinstance(raw_input, str)` branch.
    """
    raw_input = request.input
    has_attachments = bool(
        isinstance(request.metadata, dict) and request.metadata.get("attachments")
    )
    if isinstance(raw_input, str):
        last_input = raw_input
    elif raw_input:
        item = raw_input[-1]
        last_input = item if isinstance(item, str) else item.content or ""
    elif has_attachments:
        last_input = ""
    else:
        raise ValueError("No input provided")

    if not last_input.strip() and not has_attachments:
        raise ValueError("Input cannot be empty")
    return last_input


def _resolve_message_attachments(
    message: str, request_metadata: dict[str, Any], thread_id: str
) -> str:
    """Resolve ``request_metadata["attachments"]`` ids into blob content.

    Replaces the id list in place with a compact summary (for persistence
    alongside the message), stamps each blob's ``thread_id`` for the
    file-browser view, and returns ``message`` with a compact, marker-wrapped
    attachment note appended so the agent knows a file arrived and where to
    read it (see ``format_attachments_for_context``). Callers that persist or
    display ``message`` must strip everything from ``ATTACHMENT_CONTEXT_MARKER``
    onward first -- see ``chat_sessions._strip_attachment_context``.
    """
    raw_ids = request_metadata.get("attachments")
    if not isinstance(raw_ids, list) or not raw_ids:
        request_metadata.pop("attachments", None)
        return message

    manager = attachments_routes.get_attachment_manager()
    blob_store = attachments_store.get_attachment_blob_store()
    resolved = []
    for attachment_id in raw_ids:
        if not isinstance(attachment_id, str):
            continue
        attachment = manager.get_by_id(attachment_id)
        if attachment is None:
            continue
        blob_store.set_thread_id(attachment_id, thread_id)
        resolved.append(attachment)
    if not resolved:
        request_metadata.pop("attachments", None)
        return message

    request_metadata["attachments"] = [
        {
            "id": attachment.id,
            "name": attachment.original_filename,
            "mime_type": attachment.mime_type,
            "size_bytes": attachment.size_bytes,
            "error": attachment.error,
        }
        for attachment in resolved
    ]
    context_block = attachments_processing.format_attachments_for_context(resolved)
    if not context_block:
        return message
    if not message.strip():
        return f"{ATTACHMENT_CONTEXT_MARKER}\n{context_block}"
    return f"{message}\n\n{ATTACHMENT_CONTEXT_MARKER}\n{context_block}"


def _default_conversation_id(session: chat_session.SessionState) -> str:
    """Return the stable default LangGraph thread for a web session."""
    return f"thread_{session.session_id}"


def _build_chat_request_metadata(
    session: chat_session.SessionState,
    request_metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve request metadata onto a stable, session-backed thread id."""
    metadata = dict(request_metadata or {})
    requested_thread_id = str(
        metadata.get("thread_id") or metadata.get("conversation_id") or ""
    ).strip()
    thread_id = requested_thread_id or _default_conversation_id(session)
    metadata["thread_id"] = thread_id
    metadata["source"] = http_contracts.SOURCE_UI
    return thread_id, metadata


def _attach_session_cookie(
    request: Request,
    session: chat_session.SessionState,
    response: Response,
) -> Response:
    """Set the session cookie when the request has no matching cookie yet."""
    if request.cookies.get("progena_session") != session.session_id:
        chat_session.set_session_cookie(response, session)
    return response


async def _run_response_event_stream(
    message: str,
    model: str,
    request_id: str,
    response_id: str,
    thread_id: str,
    metadata: dict[str, Any] | None = None,
    *,
    stream: bool = True,
    include_reasoning: bool | None = None,
) -> None:
    """Run one dispatched generation and publish lifecycle plus response content."""
    assert lifecycle_state.client is not None, "Client must be initialized"
    cancel_event = _active_response_streams.get(request_id)
    if cancel_event is None:
        cancel_event = asyncio.Event()
        _active_response_streams[request_id] = cancel_event
    response_stream_state = _response_streams.get(request_id) if stream else None

    def response_payload(**fields: Any) -> dict[str, Any]:
        return {"id": response_id, "model": model, "thread_id": thread_id, **fields}

    def runtime_stream_timing(item: dict[str, Any]) -> dict[str, int]:
        return {
            field: value
            for field in ("runtime_emitted_at_ms", "runtime_stream_seq")
            if isinstance((value := item.get(field)), int)
        }

    def mark_response_stream_terminal() -> None:
        if response_stream_state is None:
            return
        _mark_response_stream_terminal(response_stream_state)

    def emit_cancelled() -> None:
        _clear_response_activity(request_id)
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED,
            response_payload(cancelled=True),
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        mark_response_stream_terminal()

    try:
        if cancel_event.is_set():
            emit_cancelled()
            return

        if stream:
            async for item in lifecycle_state.client.send_message_stream_events(
                message,
                request_id=request_id,
                metadata=metadata,
            ):
                if cancel_event.is_set():
                    logger.info("Response stream cancelled for %s", request_id)
                    emit_cancelled()
                    return

                item_type = str(item.get("type", ""))
                if item_type == "stream_chunk":
                    chunk = str(item.get("content", ""))
                    if not chunk:
                        continue
                    if response_stream_state is not None:
                        _append_response_stream_frame(
                            response_stream_state,
                            events_contracts.ChatContentEvent.DELTA,
                            response_payload(
                                delta=chunk,
                                request_id=request_id,
                                **runtime_stream_timing(item),
                            ),
                        )
                elif item_type == "stream_event":
                    _track_response_delegation_event(request_id, item)
                    # Structured events may carry rich image blocks. Keep them
                    # intact rather than reducing them to response text.
                    payload: dict[str, Any] = response_payload(
                        event_type=item.get("event_type", "unknown"),
                        content=item.get("content", ""),
                        metadata=item.get("metadata", {}),
                        **runtime_stream_timing(item),
                        request_id=request_id,
                    )
                    if isinstance(item.get("images"), list):
                        payload["images"] = item["images"]
                    if response_stream_state is not None:
                        _append_response_stream_frame(
                            response_stream_state,
                            events_contracts.ChatContentEvent.EVENT,
                            payload,
                        )
            if cancel_event.is_set():
                logger.info("Response stream cancelled for %s", request_id)
                emit_cancelled()
                return
        else:
            response_details = await lifecycle_state.client.send_message(
                message,
                request_id=request_id,
                metadata=metadata,
                include_reasoning=include_reasoning,
            )
            if cancel_event.is_set():
                emit_cancelled()
                return

            reasoning_text = (response_details.reasoning or "").strip()
            events_stream.broadcast_ui_event(
                events_contracts.UiEvent.CHAT_RESPONSE_DELTA,
                response_payload(
                    delta=response_details.response,
                    reasoning=reasoning_text or None,
                    reasoning_tokens=response_details.reasoning_tokens,
                ),
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
            )

        _clear_response_activity(request_id)
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.CHAT_RESPONSE_COMPLETED,
            response_payload(completed=True),
            scope="chat",
            session_id=thread_id,
            request_id=request_id,
        )
        mark_response_stream_terminal()
    except TimeoutError as error:
        logger.error("Responses API timeout: %s", error, exc_info=True)
        if cancel_event.is_set():
            emit_cancelled()
        else:
            _clear_response_activity(request_id)
            events_stream.broadcast_ui_event(
                events_contracts.UiEvent.CHAT_RESPONSE_FAILED,
                response_payload(error="Response generation timed out"),
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
            )
            mark_response_stream_terminal()
    except Exception as error:
        logger.error("Response generation error: %s", error, exc_info=True)
        if cancel_event.is_set():
            emit_cancelled()
        else:
            _clear_response_activity(request_id)
            events_stream.broadcast_ui_event(
                events_contracts.UiEvent.CHAT_RESPONSE_FAILED,
                response_payload(error=str(error)),
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
            )
            mark_response_stream_terminal()
    finally:
        _clear_response_activity(request_id)
        if _active_response_streams.get(request_id) is cancel_event:
            _active_response_streams.pop(request_id, None)
        if _response_request_ids.get(response_id) == request_id:
            _response_request_ids.pop(response_id, None)


async def _stream_text_chunks(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Yield only plain text chunks from a structured stream iterator."""
    async for item in events:
        if item.get("type") != "stream_chunk":
            continue
        chunk_text = str(item.get("content", ""))
        if chunk_text:
            yield chunk_text
