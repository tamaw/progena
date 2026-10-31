"""HTTP + SSE endpoints for the OpenAI Responses/Conversations API contract.

Served from the runtime's persistent HTTP server; every endpoint is backed by
the native LangGraph agent graph (``astream``, ``aget_state``, checkpointer).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from aiohttp import web
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.runtime import Runtime

from src.progena.documents.values import make_json_safe
from src.progena.orchestration.nodes.checkpoint import checkpoint_node
from src.progena.orchestration.state import ProgenaState
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.chat_queue import ChatQueueStore, ClaimedQueueMessage, QueueActionError
from src.progena.runtime.conversation_gate import ConversationTurnGate
from src.progena.runtime.http.responses import (
    build_response_object,
    build_sse_event,
    conversation_items_with_sources,
    error_response,
    paginate_items,
)
from src.progena.runtime.streaming.deepagent_stream import DeepAgentSSEStream
from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id
from src.progena.sessions.history import (
    ConversationSummary,
    load_conversation,
    load_conversation_messages,
)
from src.progena.sessions.messages import extract_response_status
from src.progena.sessions.models import (
    CONVERSATION_MESSAGES_FIELD,
    CONVERSATION_MESSAGES_INCLUDE,
    CONVERSATION_SUMMARIES_FIELD,
    CONVERSATION_SUMMARIES_INCLUDE,
)
from src.progena.sessions.render import merge_session_after_turn
from src.progena.sessions.status import CANCELLED_STATUS

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

EVENTS_KEEPALIVE_SECONDS = 15.0
EVENTS_SUBSCRIBER_QUEUE_SIZE = 256
DIRECT_STREAM_QUEUE_SIZE = 256
# The deepagent (langchain create_agent) node that produces assistant messages;
# a cancelled turn's partial reply is written as if this node returned it.
MODEL_NODE = "model"

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

_RESPONSE_STREAM_REPLAY_LIMIT = 512
_RESPONSE_STREAM_SUBSCRIBER_QUEUE_SIZE = 256
_RESPONSE_STREAM_RETENTION_SECONDS = 3600


class _ResponseStreamCursorExpired(ValueError):
    """A requested response-stream replay cursor has fallen out of retention."""


@dataclass
class _ResponseContentStream:
    """Bounded in-process replay plus live followers for one response."""

    frames: deque[tuple[int, str, dict[str, Any]]] = field(
        default_factory=lambda: deque(maxlen=_RESPONSE_STREAM_REPLAY_LIMIT)
    )
    subscribers: set[asyncio.Queue[tuple[int, str, dict[str, Any]] | None]] = field(
        default_factory=set
    )
    next_cursor: int = 0
    terminal: bool = False
    finished_at: float | None = None

    def append(self, event: str, data: dict[str, Any]) -> None:
        if self.terminal:
            return
        cursor = self.next_cursor
        self.next_cursor += 1
        frame = (cursor, event, data)
        self.frames.append(frame)
        for subscriber in tuple(self.subscribers):
            try:
                subscriber.put_nowait(frame)
            except asyncio.QueueFull:
                self.subscribers.discard(subscriber)

    def subscribe(
        self, cursor: int
    ) -> tuple[
        list[tuple[int, str, dict[str, Any]]],
        asyncio.Queue[tuple[int, str, dict[str, Any]] | None],
    ]:
        """Atomically validate a cursor, snapshot replay, and attach a follower."""

        if self.frames and cursor < self.frames[0][0] - 1:
            raise _ResponseStreamCursorExpired
        subscriber: asyncio.Queue[tuple[int, str, dict[str, Any]] | None] = asyncio.Queue(
            maxsize=_RESPONSE_STREAM_SUBSCRIBER_QUEUE_SIZE
        )
        replay = [frame for frame in self.frames if frame[0] > cursor]
        self.subscribers.add(subscriber)
        return replay, subscriber

    def finish(self) -> None:
        if self.terminal:
            return
        self.terminal = True
        self.finished_at = time.monotonic()
        for subscriber in tuple(self.subscribers):
            try:
                subscriber.put_nowait(None)
            except asyncio.QueueFull:
                self.subscribers.discard(subscriber)


class _ResponseContentSink:
    """``StreamResponse``-shaped sink that records a deferred response's frames."""

    def __init__(self, stream: _ResponseContentStream, *, defer_terminal: bool = False) -> None:
        self.stream = stream
        self._defer_terminal = defer_terminal
        self._terminal: tuple[str, dict[str, Any]] | None = None

    async def flush_terminal(self) -> None:
        if self._terminal is not None:
            event, data = self._terminal
            self.stream.append(event, data)
            self._terminal = None

    def defer_terminal(self, event: str, data: dict[str, Any]) -> bool:
        if not self._defer_terminal or event not in {
            "response.completed",
            "response.incomplete",
            "response.failed",
        }:
            return False
        self._terminal = (event, data)
        return True

    async def write(self, data: bytes) -> None:
        # Deferred streams have no original HTTP writer. Keepalive comments are
        # intentionally not replayed; followers produce their own keepalives.
        del data


@dataclass(frozen=True)
class _ActiveRootExecution:
    """The exact root turn a Send now command is allowed to interrupt."""

    response_id: str
    cancel_event: asyncio.Event


async def _noop() -> None:
    """No-op callback for an internal gate request."""


def _json_error(message: str, code: str, status: int, param: str | None = None) -> web.Response:
    payload, _ = error_response(message, code, status, param=param)
    return web.json_response(payload, status=status)


def _extract_attachments(metadata: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(metadata, dict):
        return None
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return None
    return [item for item in attachments if isinstance(item, dict)] or None


def _summary_occurrence_payload(summary: ConversationSummary) -> dict[str, Any]:
    """Internal runtime projection; browser descriptors are resolved by backend."""
    payload: dict[str, Any] = {
        "id": summary.id,
        "summary": summary.summary,
        "created_at_index": summary.created_at_index,
        "anchor_message_id": summary.anchor_message_id,
        "scope": summary.scope,
    }
    if summary.committed_at is not None:
        payload["committed_at"] = summary.committed_at.isoformat()
    return payload


def _extract_input_text(payload: dict[str, Any]) -> str:
    """Extract user text from the ``input`` field (string or list of items)."""
    raw = payload.get("input")
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "input_text":
                            parts.append(str(block.get("text", "")).strip())
        return "\n\n".join(parts)
    return ""


def _conversation_id(conversation: Any) -> str:
    """LangGraph thread for a request's ``conversation`` (string or ``{"id"}``).

    No conversation means a fresh, isolated one (Responses API semantics);
    callers that want continuity name the conversation explicitly.
    """
    if isinstance(conversation, dict):
        conversation = conversation.get("id")
    if isinstance(conversation, str) and conversation.strip():
        return conversation.strip()
    return f"conv_{uuid.uuid4().hex}"


def _failed_response(
    response_id: str,
    thread_id: str,
    model: str,
    metadata: dict[str, Any] | None,
    code: str,
    exc: BaseException,
) -> dict[str, Any]:
    resp_obj = build_response_object(response_id, thread_id, model, "failed", [], metadata)
    resp_obj["error"] = {"code": code, "message": str(exc)}
    return resp_obj


class RuntimeChatService:
    """Owns the runtime's Responses/Conversations API HTTP endpoints."""

    def __init__(self, loop_runner: Any, *, bridge_factory: Any = None) -> None:
        self.loop_runner = loop_runner
        self._bridge_factory = bridge_factory or DeepAgentSSEStream
        gate = getattr(loop_runner, "conversation_gate", None)
        heartbeat_context = getattr(loop_runner, "heartbeat_context", None)
        if not isinstance(gate, ConversationTurnGate):
            gate = getattr(heartbeat_context, "conversation_gate", None)
        if not isinstance(gate, ConversationTurnGate):
            gate = ConversationTurnGate()
        with contextlib.suppress(Exception):
            loop_runner.conversation_gate = gate
        if heartbeat_context is not None:
            with contextlib.suppress(Exception):
                heartbeat_context.conversation_gate = gate
        self._conversation_gate = gate
        self._session_renders: dict[str, asyncio.Task[None]] = {}
        self._session_renders_pending: set[str] = set()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._active_root_executions: dict[str, _ActiveRootExecution] = {}
        self._content_streams: dict[str, _ResponseContentStream] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._drain_task: asyncio.Task[None] | None = None
        runtime_store = getattr(
            getattr(heartbeat_context, "state_manager", None), "_runtime_store", None
        )
        self._chat_queue = (
            ChatQueueStore(runtime_store)
            if isinstance(runtime_store, LangGraphRuntimeStore)
            else None
        )
        if self._chat_queue is not None:
            self._chat_queue.set_event_publisher(self._publish_queue_snapshot)
        self._queue_drain_tasks: dict[str, asyncio.Task[None]] = {}
        self._queue_drain_wakeups: set[str] = set()
        self._queue_steering_locks: dict[str, asyncio.Lock] = {}
        self._stopping = False

    @property
    def conversation_gate(self) -> ConversationTurnGate:
        """The runtime-wide gate used by direct HTTP conversation turns."""
        return self._conversation_gate

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/v1/responses", self.handle_responses),
            web.get("/v1/responses/{response_id}/stream", self.handle_response_stream),
            web.post("/v1/responses/{response_id}/cancel", self.handle_cancel),
            web.get("/v1/conversations/{conversation_id}/items", self.handle_items),
            web.delete("/v1/conversations/{conversation_id}", self.handle_delete),
            web.get("/v1/conversations/{conversation_id}/queue", self.handle_queue),
            web.post("/v1/conversations/{conversation_id}/queue/actions", self.handle_queue_action),
            web.get("/v1/events", self.handle_events),
        ]

    async def start(self) -> None:
        self._stopping = False
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_loop())
        if self._chat_queue is not None:
            for conversation_id in await self._chat_queue.conversation_ids():
                await self._run_serialized(
                    conversation_id,
                    lambda conversation_id=conversation_id: self._recover_queue(conversation_id),
                    on_queued=_noop,
                )
                self._schedule_queue_drain(conversation_id)

    async def stop(self) -> None:
        self._stopping = True
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None
        while self._queue_drain_tasks:
            drains = tuple(self._queue_drain_tasks.values())
            for task in drains:
                task.cancel()
            for task in drains:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for conversation_id, task in tuple(self._queue_drain_tasks.items()):
                if task in drains and task.done():
                    self._queue_drain_tasks.pop(conversation_id, None)
        self._queue_drain_wakeups.clear()
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)
        self._subscribers.clear()

    # -- POST /v1/responses ---------------------------------------------

    async def handle_responses(self, request: web.Request) -> web.StreamResponse | web.Response:
        try:
            payload = await request.json()
        except Exception:
            return _json_error("Invalid JSON body", "bad_request", 400)
        if not isinstance(payload, dict):
            return _json_error("Expected a JSON object body", "bad_request", 400)

        message = _extract_input_text(payload)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        attachments = _extract_attachments(metadata)

        if not message and not attachments:
            return _json_error("input is required", "bad_request", 400)

        received_at = time.time()
        source = str(metadata.get("source") or "").strip() if metadata else ""
        if source not in ("ui", "telegram", "api"):
            source = None

        if not self._can_run_deepagent_direct():
            return _json_error("DeepAgent graph is not available", "graph_unavailable", 503)

        response_id = (
            str((metadata or {}).get("request_id") or "").strip() or f"resp_{uuid.uuid4().hex}"
        )
        model = str(payload.get("model") or "progena-v1")
        stream = bool(payload.get("stream", False))
        thread_id = _conversation_id(payload.get("conversation"))

        if self._conversation_gate.is_active(thread_id):
            if self._chat_queue is None:
                return _json_error("Durable chat queue is not available", "queue_unavailable", 503)
            _snapshot, _message_id = await self._chat_queue.enqueue(
                thread_id,
                text=message,
                attachments=attachments or [],
                metadata=metadata,
                source=source,
                received_at=received_at,
                response_id=response_id,
                model=model,
            )
            self._ensure_content_stream(response_id)
            self._schedule_queue_drain(thread_id)
            queued_response = build_response_object(
                response_id, thread_id, model, "queued", [], metadata
            )
            if not stream:
                return web.json_response(make_json_safe(queued_response))
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
            )
            await response.prepare(request)
            await self._write_frame(
                response,
                "response.created",
                build_sse_event("response.created", {"response": queued_response}, 0),
            )
            await self._write_frame(
                response,
                "response.queued",
                build_sse_event("response.queued", {"response": queued_response}, 1),
            )
            # This request has handed its input to the durable runtime queue.
            # It must terminally close rather than keep a second foreground
            # stream alive until a future model boundary consumes it.
            await self._write_frame(
                response,
                "response.completed",
                build_sse_event("response.completed", {"response": queued_response}, 2),
            )
            return response

        cancel_event = self._cancel_events.setdefault(response_id, asyncio.Event())
        try:
            if not stream:
                result = await self._run_serialized(
                    thread_id,
                    lambda: self._run_turn(
                        message,
                        response_id,
                        thread_id,
                        model,
                        metadata,
                        cancel_event,
                        received_at,
                        source=source,
                        message_id=f"user_{response_id}",
                    ),
                    on_queued=lambda: self._notify_queued_request(response_id, streaming=False),
                )
                return web.json_response(make_json_safe(result))

            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
            )
            await response.prepare(request)
            sequence = 0

            async def _lifecycle(event_type: str, status: str) -> None:
                nonlocal sequence
                await self._write_frame(
                    response,
                    event_type,
                    build_sse_event(
                        event_type,
                        {
                            "response": build_response_object(
                                response_id, thread_id, model, status, [], metadata
                            )
                        },
                        sequence,
                    ),
                )
                sequence += 1

            async def _on_queued() -> None:
                await _lifecycle("response.created", "queued")
                await _lifecycle("response.queued", "queued")
                await self._notify_queued_request(response_id, streaming=True)

            async def _turn() -> None:
                self._activate_root(thread_id, response_id, cancel_event)
                try:
                    if sequence == 0:
                        await _lifecycle("response.created", "in_progress")
                    await _lifecycle("response.in_progress", "in_progress")
                    await self._stream_turn(
                        response,
                        message,
                        response_id,
                        thread_id,
                        model,
                        metadata,
                        cancel_event,
                        received_at,
                        start_sequence=sequence,
                        source=source,
                        message_id=f"user_{response_id}",
                    )
                finally:
                    self._deactivate_root(thread_id, response_id, cancel_event)

            await self._run_serialized(thread_id, _turn, on_queued=_on_queued)
            return response
        finally:
            self._cancel_events.pop(response_id, None)

    async def _run_serialized(
        self,
        thread_id: str,
        turn: Callable[[], Awaitable[_T]],
        *,
        on_queued: Callable[[], Awaitable[None]],
    ) -> _T:
        """Run an HTTP turn through the runtime's shared checkpoint gate."""
        return await self._conversation_gate.run(thread_id, turn, on_queued=on_queued)

    async def _stream_turn(
        self,
        response: web.StreamResponse | _ResponseContentSink,
        message: str,
        response_id: str,
        thread_id: str,
        model: str,
        metadata: dict[str, Any] | None,
        cancel_event: asyncio.Event,
        received_at: float,
        start_sequence: int,
        source: str | None = None,
        message_id: str | None = None,
        *,
        resume: bool = False,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            output: list[dict[str, Any]] = []
            if resume:
                before_count = await self._message_count(thread_id)
                output = await self._persist_cancelled_turn(
                    thread_id,
                    response_id,
                    "",
                    before_count,
                    required=True,
                )
            cancelled_response = build_response_object(
                response_id, thread_id, model, "cancelled", output, metadata
            )
            with contextlib.suppress(ConnectionResetError, ConnectionError):
                await self._write_frame(
                    response,
                    "response.incomplete",
                    build_sse_event(
                        "response.incomplete",
                        {"response": cancelled_response},
                        start_sequence,
                    ),
                )
            return cancelled_response
        bridge = self._create_direct_stream_bridge()
        cancelled = False
        bridge_cancelled = False
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=DIRECT_STREAM_QUEUE_SIZE)

        seq = start_sequence
        try:
            # Message count before the turn, so the completed response's
            # ``output`` is exactly what this turn added to the thread.
            before_count = await self._message_count(thread_id)
        except Exception as exc:
            logger.error("State read error for %s: %s", response_id, exc, exc_info=True)
            failed = _failed_response(response_id, thread_id, model, metadata, "state_error", exc)
            await self._write_failed(
                response, response_id, thread_id, model, metadata, seq, "state_error", exc
            )
            return failed

        token = set_current_thread_id(thread_id)
        text_item_opened = False
        current_text = ""
        item_id = f"msg_{response_id}"
        output_index = 0
        content_index = 0

        try:

            async def _produce() -> None:
                try:
                    stream_kwargs: dict[str, Any] = {
                        "thread_id": thread_id,
                        "received_at": received_at,
                        "attachments": _extract_attachments(metadata),
                        "source": source,
                        "message_id": message_id,
                        "metadata": metadata,
                    }
                    if resume:
                        stream_kwargs["resume"] = True
                    async for item in bridge.stream(message, **stream_kwargs):
                        await queue.put(("item", item))
                except asyncio.CancelledError:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(("cancelled", None))
                    raise
                except Exception as exc:
                    await queue.put(("error", exc))
                else:
                    await queue.put(("done", None))

            producer = asyncio.create_task(_produce())
            try:
                while True:
                    if cancel_event.is_set():
                        cancelled = True
                        break

                    read_task = asyncio.create_task(queue.get())
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    done, pending = await asyncio.wait(
                        (read_task, cancel_task),
                        timeout=EVENTS_KEEPALIVE_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    for task in pending:
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                    if not done:
                        await response.write(b": keepalive\n\n")
                        continue
                    if cancel_task in done:
                        cancelled = True
                        break
                    kind, payload = read_task.result()

                    if kind == "done":
                        break

                    if kind == "cancelled":
                        bridge_cancelled = True
                        break

                    if kind == "error":
                        exc = payload
                        if isinstance(exc, (ConnectionResetError, ConnectionError)):
                            cancelled = True
                            break
                        logger.error("Streaming error for %s: %s", response_id, exc, exc_info=exc)
                        resp_obj = build_response_object(
                            response_id, thread_id, model, "failed", [], metadata
                        )
                        resp_obj["error"] = {"code": "stream_error", "message": str(exc)}
                        with contextlib.suppress(ConnectionResetError, ConnectionError):
                            await self._write_frame(
                                response,
                                "response.failed",
                                build_sse_event("response.failed", {"response": resp_obj}, seq),
                            )
                        return resp_obj

                    item = payload
                    runtime_emitted_at_ms = int(time.time() * 1000)
                    item_type = str(item.get("type", "stream_chunk"))

                    if item_type == "stream_event":
                        event_payload: dict[str, Any] = {
                            "event_type": str(item.get("event_type", "unknown")),
                            "content": str(item.get("content", "")),
                            "metadata": item.get("metadata"),
                            "runtime_emitted_at_ms": runtime_emitted_at_ms,
                            "runtime_stream_seq": seq,
                        }
                        images = item.get("images")
                        if isinstance(images, list) and images:
                            event_payload["images"] = images
                        await self._write_frame(
                            response,
                            "progena.stream_event",
                            build_sse_event("progena.stream_event", event_payload, seq),
                        )
                        seq += 1
                        continue

                    is_final = bool(item.get("is_final"))
                    content = str(item.get("content", ""))

                    if content:
                        if not text_item_opened:
                            text_item_opened = True
                            await self._write_frame(
                                response,
                                "response.output_item.added",
                                build_sse_event(
                                    "response.output_item.added",
                                    {
                                        "output_index": output_index,
                                        "item": {
                                            "id": item_id,
                                            "type": "message",
                                            "role": "assistant",
                                            "status": "in_progress",
                                            "content": [],
                                        },
                                    },
                                    seq,
                                ),
                            )
                            seq += 1
                            await self._write_frame(
                                response,
                                "response.content_part.added",
                                build_sse_event(
                                    "response.content_part.added",
                                    {
                                        "item_id": item_id,
                                        "output_index": output_index,
                                        "content_index": content_index,
                                        "part": {
                                            "type": "output_text",
                                            "text": "",
                                            "annotations": [],
                                        },
                                    },
                                    seq,
                                ),
                            )
                            seq += 1

                        current_text += content
                        await self._write_frame(
                            response,
                            "response.output_text.delta",
                            build_sse_event(
                                "response.output_text.delta",
                                {
                                    "item_id": item_id,
                                    "output_index": output_index,
                                    "content_index": content_index,
                                    "delta": content,
                                    "logprobs": [],
                                    "runtime_emitted_at_ms": runtime_emitted_at_ms,
                                    "runtime_stream_seq": seq,
                                },
                                seq,
                            ),
                        )
                        seq += 1

                    if is_final:
                        break
            except (ConnectionResetError, ConnectionError):
                cancelled = True
            except Exception as exc:
                logger.error("Streaming error for %s: %s", response_id, exc, exc_info=True)
                resp_obj = build_response_object(
                    response_id, thread_id, model, "failed", [], metadata
                )
                resp_obj["error"] = {"code": "stream_error", "message": str(exc)}
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response,
                        "response.failed",
                        build_sse_event("response.failed", {"response": resp_obj}, seq),
                    )
                return resp_obj
            finally:
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

            if bridge_cancelled and not cancel_event.is_set():
                logger.info("Direct DeepAgent bridge cancelled for %s", response_id)
                cancelled = True

            if cancelled or cancel_event.is_set():
                logger.info("Direct DeepAgent stream cancelled for %s", response_id)
                output = await self._persist_cancelled_turn(
                    thread_id, response_id, current_text, before_count, required=resume
                )
                resp_obj = build_response_object(
                    response_id, thread_id, model, "cancelled", output, metadata
                )
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response,
                        "response.incomplete",
                        build_sse_event("response.incomplete", {"response": resp_obj}, seq),
                    )
                return resp_obj

            # Close open text item
            if text_item_opened:
                await self._write_frame(
                    response,
                    "response.output_text.done",
                    build_sse_event(
                        "response.output_text.done",
                        {
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "text": current_text,
                            "logprobs": [],
                        },
                        seq,
                    ),
                )
                seq += 1
                await self._write_frame(
                    response,
                    "response.content_part.done",
                    build_sse_event(
                        "response.content_part.done",
                        {
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "part": {
                                "type": "output_text",
                                "text": current_text,
                                "annotations": [],
                            },
                        },
                        seq,
                    ),
                )
                seq += 1
                await self._write_frame(
                    response,
                    "response.output_item.done",
                    build_sse_event(
                        "response.output_item.done",
                        {
                            "output_index": output_index,
                            "item": {
                                "id": item_id,
                                "type": "message",
                                "role": "assistant",
                                "status": "completed",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": current_text,
                                        "annotations": [],
                                    }
                                ],
                            },
                        },
                        seq,
                    ),
                )
                seq += 1

            try:
                await self._complete_direct_graph_cycle(bridge, thread_id)
                output = await self._turn_output(thread_id, before_count)
            except Exception as exc:
                logger.error("Checkpoint error for %s: %s", response_id, exc, exc_info=True)
                failed = _failed_response(
                    response_id, thread_id, model, metadata, "checkpoint_error", exc
                )
                await self._write_failed(
                    response, response_id, thread_id, model, metadata, seq, "checkpoint_error", exc
                )
                return failed

            resp_obj = build_response_object(
                response_id, thread_id, model, "completed", output, metadata
            )
            with contextlib.suppress(ConnectionResetError, ConnectionError):
                await self._write_frame(
                    response,
                    "response.completed",
                    build_sse_event("response.completed", {"response": resp_obj}, seq),
                )
            return resp_obj
        finally:
            reset_current_thread_id(token)
            self._fire_and_forget_session_merge(thread_id)
            self._schedule_queue_drain(thread_id)

    async def _run_turn(
        self,
        message: str,
        response_id: str,
        thread_id: str,
        model: str,
        metadata: dict[str, Any] | None,
        cancel_event: asyncio.Event,
        received_at: float,
        source: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            return build_response_object(response_id, thread_id, model, "cancelled", [], metadata)
        bridge = self._create_direct_stream_bridge()
        try:
            before_count = await self._message_count(thread_id)
        except Exception as exc:
            logger.error("State read error for %s: %s", response_id, exc, exc_info=True)
            return _failed_response(response_id, thread_id, model, metadata, "state_error", exc)

        self._activate_root(thread_id, response_id, cancel_event)
        token = set_current_thread_id(thread_id)
        try:
            partial_text = ""
            try:
                stream = bridge.stream(
                    message,
                    thread_id=thread_id,
                    received_at=received_at,
                    attachments=_extract_attachments(metadata),
                    source=source,
                    message_id=message_id,
                    metadata=metadata,
                )
                # A cancel must close the graph run before its cancelled reply
                # is written to the thread.
                async with contextlib.aclosing(stream):
                    async for item in stream:
                        if item.get("type", "stream_chunk") == "stream_chunk":
                            partial_text += str(item.get("content", ""))
                        if cancel_event.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Turn error for %s: %s", response_id, exc, exc_info=True)
                failed = build_response_object(
                    response_id, thread_id, model, "failed", [], metadata
                )
                failed["error"] = {"code": "stream_error", "message": str(exc)}
                return failed

            if cancel_event.is_set():
                output = await self._persist_cancelled_turn(
                    thread_id, response_id, partial_text, before_count
                )
                return build_response_object(
                    response_id, thread_id, model, "cancelled", output, metadata
                )

            try:
                await self._complete_direct_graph_cycle(bridge, thread_id)
                output = await self._turn_output(thread_id, before_count)
            except Exception as exc:
                logger.error("Checkpoint error for %s: %s", response_id, exc, exc_info=True)
                return _failed_response(
                    response_id, thread_id, model, metadata, "checkpoint_error", exc
                )
            return build_response_object(
                response_id, thread_id, model, "completed", output, metadata
            )
        finally:
            reset_current_thread_id(token)
            self._deactivate_root(thread_id, response_id, cancel_event)
            self._fire_and_forget_session_merge(thread_id)
            self._schedule_queue_drain(thread_id)

    async def _persist_cancelled_turn(
        self,
        thread_id: str,
        response_id: str,
        partial_text: str,
        before_count: int,
        *,
        required: bool = False,
    ) -> list[dict[str, Any]]:
        """Save what a cancelled turn had streamed as a cancelled assistant message.

        The interrupted model step never committed, so the thread would
        otherwise end at the user's message. Writing the partial reply as if
        the model node had returned it (native ``aupdate_state``) keeps it in
        history for the user, and as context for the model's next turn
        ("continue"). Returns the response ``output`` items for this turn.
        """
        message = AIMessage(
            id=f"msg_{response_id}",
            content=partial_text,
            response_metadata={"status": CANCELLED_STATUS},
        )
        try:
            await self.loop_runner.graph.aupdate_state(
                {"configurable": {"thread_id": thread_id}},
                {"messages": [message]},
                as_node=MODEL_NODE,
            )
            return await self._turn_output(thread_id, before_count)
        except Exception as exc:
            logger.warning("Saving cancelled turn %s failed: %s", response_id, exc)
            if required:
                raise
            return []

    def _fire_and_forget_session_merge(self, thread_id: str) -> None:
        """Re-render the thread and merge ``content``/``last_activity`` into the session row.

        Renders of one thread never overlap: a turn finishing while its thread
        renders queues one more render, so the last merge reflects the last turn.
        """
        context = getattr(self.loop_runner, "heartbeat_context", None)
        state_manager = getattr(context, "state_manager", None)
        checkpointer = getattr(self.loop_runner.graph, "checkpointer", None)
        if state_manager is None or checkpointer is None:
            return
        if thread_id in self._session_renders:
            self._session_renders_pending.add(thread_id)
            return
        self._session_renders[thread_id] = asyncio.create_task(
            self._render_session_until_current(thread_id, checkpointer, state_manager)
        )

    async def _render_session_until_current(
        self, thread_id: str, checkpointer: Any, state_manager: Any
    ) -> None:
        try:
            while True:
                self._session_renders_pending.discard(thread_id)
                await merge_session_after_turn(thread_id, checkpointer, state_manager)
                if thread_id not in self._session_renders_pending:
                    return
        finally:
            # No await between the pending check and this: a later turn either
            # re-ran the loop above or starts a fresh render.
            self._session_renders.pop(thread_id, None)

    async def _ensure_session_merged(self, thread_id: str) -> None:
        """Wait until the browser-visible session record includes the turn."""
        self._fire_and_forget_session_merge(thread_id)
        task = self._session_renders.get(thread_id)
        if task is not None:
            await asyncio.shield(task)

    async def _message_count(self, thread_id: str) -> int:
        state = await self.loop_runner.graph.aget_state({"configurable": {"thread_id": thread_id}})
        return len(state.values.get("messages") or [])

    async def _turn_output(self, thread_id: str, before_count: int) -> list[dict[str, Any]]:
        """Response ``output``: items for what the model added this turn.

        The turn's own user input is part of the conversation, not the
        response output (Responses API semantics), so human messages are
        excluded.
        """
        state = await self.loop_runner.graph.aget_state({"configurable": {"thread_id": thread_id}})
        messages = state.values.get("messages") or []
        return [
            item
            for index, item in conversation_items_with_sources(messages, thread_id)
            if index >= before_count and not isinstance(messages[index], HumanMessage)
        ]

    # -- GET /v1/responses/{response_id}/stream -------------------------

    async def handle_response_stream(
        self, request: web.Request
    ) -> web.StreamResponse | web.Response:
        response_id = request.match_info["response_id"]
        stream = self._content_streams.get(response_id)
        if stream is None:
            return _json_error(f"Response '{response_id}' not found", "not_found", 404)
        raw_cursor = request.query.get("cursor", request.headers.get("Last-Event-ID", ""))
        try:
            cursor = int(raw_cursor) if raw_cursor else -1
        except ValueError:
            return _json_error("cursor must be an integer", "bad_request", 400, param="cursor")
        if cursor < -1:
            return _json_error("cursor must be at least -1", "bad_request", 400, param="cursor")
        try:
            replay, subscriber = stream.subscribe(cursor)
        except _ResponseStreamCursorExpired:
            return _json_error(
                "cursor has expired; restart the response stream",
                "cursor_expired",
                409,
                param="cursor",
            )

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
        )
        try:
            await response.prepare(request)
        except BaseException:
            stream.subscribers.discard(subscriber)
            raise
        try:
            for frame_cursor, event, data in replay:
                await self._write_replay_frame(response, frame_cursor, event, data)
            while not (stream.terminal and subscriber.empty()):
                if subscriber not in stream.subscribers:
                    await self._write_replay_frame(
                        response,
                        stream.next_cursor,
                        "response.failed",
                        {"error": {"code": "stream_desynchronized"}},
                    )
                    break
                try:
                    item = await asyncio.wait_for(
                        subscriber.get(), timeout=EVENTS_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                if item is None:
                    if stream.terminal and subscriber.empty():
                        break
                    continue
                frame_cursor, event, data = item
                await self._write_replay_frame(response, frame_cursor, event, data)
        except (ConnectionResetError, ConnectionError):
            pass
        finally:
            stream.subscribers.discard(subscriber)
        return response

    # -- POST /v1/responses/{response_id}/cancel ------------------------

    async def handle_cancel(self, request: web.Request) -> web.Response:
        response_id = request.match_info["response_id"]
        cancel_event = self._cancel_events.get(response_id)
        if cancel_event is not None:
            cancel_event.set()
            return web.json_response(
                {"id": response_id, "object": "response", "status": "cancelled"}
            )
        payload, status = error_response(f"Response '{response_id}' not found", "not_found", 404)
        return web.json_response(payload, status=status)

    # -- Durable chat queue ---------------------------------------------

    async def handle_queue(self, request: web.Request) -> web.Response:
        if self._chat_queue is None:
            return _json_error("Durable chat queue is not available", "queue_unavailable", 503)
        conversation_id = request.match_info["conversation_id"]
        return web.json_response(make_json_safe(await self._chat_queue.snapshot(conversation_id)))

    async def handle_queue_action(self, request: web.Request) -> web.Response:
        if self._chat_queue is None:
            return _json_error("Durable chat queue is not available", "queue_unavailable", 503)
        try:
            payload = await request.json()
        except Exception:
            return _json_error("Invalid JSON body", "bad_request", 400)
        if not isinstance(payload, dict):
            return _json_error("Expected a JSON object body", "bad_request", 400)
        conversation_id = request.match_info["conversation_id"]
        before = await self._chat_queue.snapshot(conversation_id)
        deleted = next(
            (
                item
                for item in before.get("messages", [])
                if isinstance(item, dict)
                and item.get("id") == payload.get("message_id")
                and item.get("status") == "queued"
            ),
            None,
        )
        steering_lock = (
            self._queue_steering_locks.setdefault(conversation_id, asyncio.Lock())
            if payload.get("action") == "send_now"
            else None
        )
        if steering_lock is not None:
            await steering_lock.acquire()
        try:
            active = (
                self._active_root_executions.get(conversation_id)
                if payload.get("action") == "send_now"
                else None
            )
            try:
                snapshot, applied = await self._chat_queue.action(
                    conversation_id, payload, include_applied=True
                )
            except QueueActionError as exc:
                error, _status = error_response(str(exc), exc.code, exc.status)
                if exc.snapshot is not None:
                    error["snapshot"] = exc.snapshot
                return web.json_response(make_json_safe(error), status=exc.status)
            if payload.get("action") == "send_now" and applied and active is not None:
                # A direct root has no queue claim, so its cancellation remains
                # in-memory; there is no pending queue claim to recover after restart.
                self._cancel_captured_root(conversation_id, active)
        finally:
            if steering_lock is not None:
                steering_lock.release()
        if payload.get("action") == "delete" and applied and isinstance(deleted, dict):
            await self._emit_deleted_queue_terminal(
                ChatQueueStore._claim_from_item(conversation_id, deleted)
            )
        self._schedule_queue_drain(conversation_id)
        return web.json_response(make_json_safe(snapshot))

    async def _checkpoint_queue_state(
        self, conversation_id: str
    ) -> tuple[set[str], dict[str, str], bool]:
        """Return committed inputs, proven terminal outcomes, and pending work."""
        graph = getattr(self.loop_runner, "graph", None)
        if graph is None:
            return set(), {}, False
        state = await graph.aget_state({"configurable": {"thread_id": conversation_id}})
        messages = state.values.get("messages") or []
        committed = {
            message.id
            for message in messages
            if isinstance(message, HumanMessage) and isinstance(message.id, str) and message.id
        }
        pending = bool(getattr(state, "next", ()))
        terminal: dict[str, str] = {}
        if not pending:
            for index, message in enumerate(messages):
                if not isinstance(message, HumanMessage) or not isinstance(message.id, str):
                    continue
                next_human = next(
                    (
                        later_index
                        for later_index, later in enumerate(messages[index + 1 :], index + 1)
                        if isinstance(later, HumanMessage)
                    ),
                    len(messages),
                )
                final = next(
                    (
                        later
                        for later in reversed(messages[index + 1 : next_human])
                        if isinstance(later, AIMessage) and not getattr(later, "tool_calls", None)
                    ),
                    None,
                )
                if final is not None:
                    status = extract_response_status(final)
                    terminal[message.id] = "completed" if status == "unknown" else status
        if pending:
            for index, message in enumerate(messages):
                if not isinstance(message, HumanMessage) or not isinstance(message.id, str):
                    continue
                next_human = next(
                    (
                        position
                        for position, later in enumerate(messages[index + 1 :], index + 1)
                        if isinstance(later, HumanMessage)
                    ),
                    len(messages),
                )
                final = next(
                    (
                        later
                        for later in reversed(messages[index + 1 : next_human])
                        if isinstance(later, AIMessage) and not getattr(later, "tool_calls", None)
                    ),
                    None,
                )
                if final is not None:
                    status = extract_response_status(final)
                    if status in {"cancelled", "failed", "interrupted"}:
                        terminal[message.id] = status
        return committed, terminal, pending

    async def _checkpoint_message_ids(self, conversation_id: str) -> set[str]:
        committed, _terminal, _pending = await self._checkpoint_queue_state(conversation_id)
        return committed

    async def _recover_queue(self, conversation_id: str) -> tuple[set[str], bool]:
        committed, terminal_outcomes, pending = await self._checkpoint_queue_state(conversation_id)
        if self._chat_queue is not None:
            cancelled = await self._chat_queue.cancel_requested_claims(conversation_id)
            cancelled_ids = {claimed.id for claimed in cancelled}
            for claimed in cancelled:
                await self._settle_recovered_cancellation(
                    claimed, already_terminal=claimed.id in terminal_outcomes
                )

            before = await self._chat_queue.snapshot(conversation_id)
            settled = [
                ChatQueueStore._claim_from_item(conversation_id, item)
                for item in before.get("messages", [])
                if (
                    isinstance(item, dict)
                    and item.get("status") == "sending"
                    and str(item.get("id")) in terminal_outcomes
                    and str(item.get("id")) not in cancelled_ids
                )
            ]
            await self._chat_queue.recover(
                conversation_id, committed, terminal_message_ids=terminal_outcomes
            )
            if settled:
                await self._ensure_session_merged(conversation_id)
            for claimed in settled:
                await self._emit_recovered_terminal(claimed, terminal_outcomes[claimed.id])
        return committed, pending

    async def _settle_recovered_cancellation(
        self, claimed: ClaimedQueueMessage, *, already_terminal: bool
    ) -> None:
        """Persist and publish an acknowledged Send now cancellation after restart."""

        if already_terminal:
            output: list[dict[str, Any]] = []
        else:
            before_count = await self._claimed_turn_before_count(claimed)
            output = await self._persist_cancelled_turn(
                claimed.conversation_id,
                claimed.response_id,
                "",
                before_count,
                required=True,
            )
        assert self._chat_queue is not None
        await self._chat_queue.settle_claim(claimed.conversation_id, claimed.id)
        await self._ensure_session_merged(claimed.conversation_id)
        await self._emit_recovered_cancellation(claimed, output)

    async def _claimed_turn_before_count(self, claimed: ClaimedQueueMessage) -> int:
        state = await self.loop_runner.graph.aget_state(
            {"configurable": {"thread_id": claimed.conversation_id}}
        )
        messages = state.values.get("messages") or []
        return next(
            (
                index
                for index, message in enumerate(messages)
                if isinstance(message, HumanMessage) and message.id == claimed.id
            ),
            len(messages),
        )

    async def _emit_recovered_cancellation(
        self, claimed: ClaimedQueueMessage, output: list[dict[str, Any]]
    ) -> None:
        stream = self._ensure_content_stream(claimed.response_id)
        metadata = (
            {**claimed.metadata, "attachments": claimed.attachments}
            if claimed.attachments
            else claimed.metadata
        )
        response = build_response_object(
            claimed.response_id,
            claimed.conversation_id,
            claimed.model,
            "cancelled",
            output,
            metadata,
        )
        await self._write_frame(
            _ResponseContentSink(stream),
            "response.incomplete",
            build_sse_event("response.incomplete", {"response": response}, 0),
        )
        stream.finish()
        await self._publish_queue_execution("chat_queue_execution_cancelled", claimed, response)

    async def _emit_recovered_terminal(self, claimed: ClaimedQueueMessage, status: str) -> None:
        stream = self._ensure_content_stream(claimed.response_id)
        metadata = (
            {**claimed.metadata, "attachments": claimed.attachments}
            if claimed.attachments
            else claimed.metadata
        )
        normalized = (
            "failed" if status == "failed" else "cancelled" if status != "completed" else status
        )
        response = build_response_object(
            claimed.response_id, claimed.conversation_id, claimed.model, normalized, [], metadata
        )
        event = (
            "response.completed"
            if normalized == "completed"
            else "response.failed"
            if normalized == "failed"
            else "response.incomplete"
        )
        await self._write_frame(
            _ResponseContentSink(stream), event, build_sse_event(event, {"response": response}, 0)
        )
        stream.finish()
        lifecycle = (
            "chat_queue_execution_completed"
            if normalized == "completed"
            else "chat_queue_execution_failed"
            if normalized == "failed"
            else "chat_queue_execution_cancelled"
        )
        await self._publish_queue_execution(lifecycle, claimed, response)

    async def _emit_deleted_queue_terminal(self, claimed: ClaimedQueueMessage) -> None:
        stream = self._ensure_content_stream(claimed.response_id)
        response = build_response_object(
            claimed.response_id,
            claimed.conversation_id,
            claimed.model,
            "cancelled",
            [],
            claimed.metadata,
        )
        await self._write_frame(
            _ResponseContentSink(stream),
            "response.incomplete",
            build_sse_event("response.incomplete", {"response": response}, 0),
        )
        stream.finish()
        await self._publish_queue_execution("chat_queue_execution_cancelled", claimed, response)

    def _schedule_queue_drain(self, conversation_id: str) -> None:
        if self._stopping or self._chat_queue is None:
            return
        if conversation_id in self._queue_drain_tasks:
            self._queue_drain_wakeups.add(conversation_id)
            return
        task = asyncio.create_task(self._drain_conversation_queue(conversation_id))
        self._queue_drain_tasks[conversation_id] = task

    async def _drain_conversation_queue(self, conversation_id: str) -> None:
        try:
            while not self._stopping:
                progressed = await self._run_serialized(
                    conversation_id,
                    lambda: self._run_next_queued_turn(conversation_id),
                    on_queued=_noop,
                )
                if not progressed:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queued chat drain failed for %s", conversation_id)
        finally:
            self._queue_drain_tasks.pop(conversation_id, None)
            if not self._stopping and conversation_id in self._queue_drain_wakeups:
                self._queue_drain_wakeups.discard(conversation_id)
                self._schedule_queue_drain(conversation_id)

    async def _run_next_queued_turn(self, conversation_id: str) -> bool:
        if self._chat_queue is None:
            return False
        committed, pending = await self._recover_queue(conversation_id)
        claimed = await self._chat_queue.claim_recoverable(conversation_id, committed)
        resume = claimed is not None and pending
        if claimed is None:
            claimed = await self._chat_queue.claim_next(conversation_id)
        if claimed is None:
            return False
        stream = self._ensure_content_stream(claimed.response_id)
        sink = _ResponseContentSink(stream, defer_terminal=True)
        await self._publish_queue_execution("chat_queue_execution_started", claimed, None)
        try:
            result = await self._run_claimed_turn(claimed, sink=sink, resume=resume)
            await self._ensure_session_merged(conversation_id)
            await sink.flush_terminal()
            status = str(result.get("status") or "failed")
            await self._chat_queue.settle_claim(conversation_id, claimed.id)
            if status == "completed":
                await self._publish_queue_execution(
                    "chat_queue_execution_completed", claimed, result
                )
            elif status == "cancelled":
                await self._publish_queue_execution(
                    "chat_queue_execution_cancelled", claimed, result
                )
            else:
                await self._publish_queue_execution("chat_queue_execution_failed", claimed, result)
            return True
        finally:
            stream.finish()

    async def _run_claimed_turn(
        self, claimed: ClaimedQueueMessage, *, sink: _ResponseContentSink, resume: bool
    ) -> dict[str, Any]:
        metadata = claimed.metadata
        if claimed.attachments:
            metadata = {**metadata, "attachments": claimed.attachments}
        cancel_event = asyncio.Event()
        self._cancel_events[claimed.response_id] = cancel_event
        self._activate_root(claimed.conversation_id, claimed.response_id, cancel_event)
        try:
            steering_lock = self._queue_steering_locks.setdefault(
                claimed.conversation_id, asyncio.Lock()
            )
            async with steering_lock:
                if self._chat_queue is not None and await self._chat_queue.is_cancel_requested(
                    claimed.conversation_id, claimed.id
                ):
                    cancel_event.set()
            await self._write_frame(
                sink,
                "response.created",
                build_sse_event(
                    "response.created",
                    {
                        "response": build_response_object(
                            claimed.response_id,
                            claimed.conversation_id,
                            claimed.model,
                            "in_progress",
                            [],
                            metadata,
                        )
                    },
                    0,
                ),
            )
            await self._write_frame(
                sink,
                "response.in_progress",
                build_sse_event(
                    "response.in_progress",
                    {
                        "response": build_response_object(
                            claimed.response_id,
                            claimed.conversation_id,
                            claimed.model,
                            "in_progress",
                            [],
                            metadata,
                        )
                    },
                    1,
                ),
            )
            return await self._stream_turn(
                sink,
                claimed.text,
                claimed.response_id,
                claimed.conversation_id,
                claimed.model,
                metadata,
                cancel_event,
                claimed.received_at if claimed.received_at is not None else time.time(),
                start_sequence=2,
                source=claimed.source,
                message_id=claimed.id,
                resume=resume,
            )
        finally:
            self._deactivate_root(claimed.conversation_id, claimed.response_id, cancel_event)
            self._cancel_events.pop(claimed.response_id, None)

    async def _publish_queue_snapshot(self, snapshot: dict[str, Any]) -> None:
        queues = getattr(self.loop_runner, "queues", None)
        if queues is not None:
            await queues.push_event("chat_queue_updated", "", snapshot)

    async def _publish_queue_execution(
        self,
        event_type: str,
        claimed: ClaimedQueueMessage,
        response: dict[str, Any] | None,
    ) -> None:
        queues = getattr(self.loop_runner, "queues", None)
        if queues is None:
            return
        metadata: dict[str, Any] = {
            "conversation_id": claimed.conversation_id,
            "request_id": claimed.response_id,
            "content_request_id": claimed.response_id,
            "message_id": claimed.id,
        }
        if event_type == "chat_queue_execution_started":
            metadata["queued_message"] = {
                "id": claimed.id,
                "text": claimed.text,
                "attachments": make_json_safe(claimed.attachments),
            }
        if response is not None:
            metadata["response"] = response
        await queues.push_event(event_type, "", metadata)

    # -- GET /v1/conversations/{conversation_id}/items -------------------

    async def handle_items(self, request: web.Request) -> web.Response:
        conversation_id = request.match_info["conversation_id"]
        graph = getattr(self.loop_runner, "graph", None)
        if graph is None:
            return _json_error("Graph is not available", "graph_unavailable", 503)

        # Parse query params
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            return _json_error("limit must be an integer", "bad_request", 400)
        if limit < 1 or limit > 100:
            return _json_error("limit must be between 1 and 100", "bad_request", 400)

        order = request.query.get("order", "desc")
        if order not in ("asc", "desc"):
            return _json_error("order must be 'asc' or 'desc'", "bad_request", 400, param="order")

        after = request.query.get("after") or None

        include_set = {
            value for key in ("include", "include[]") for value in request.query.getall(key, [])
        }

        include_langchain = CONVERSATION_MESSAGES_INCLUDE in include_set
        include_summaries = CONVERSATION_SUMMARIES_INCLUDE in include_set

        checkpoint_id = request.query.get("checkpoint_id") or None
        if include_summaries:
            conversation = await load_conversation(
                graph.checkpointer,
                conversation_id,
                checkpoint_id=checkpoint_id,
                include_summaries=True,
                include_subagents=True,
            )
            if conversation is None:
                return _json_error("Conversation not found", "not_found", 404)
            messages = conversation.root.messages
            summaries = conversation.summaries
        elif checkpoint_id and isinstance(getattr(graph, "checkpointer", None), AsyncPostgresSaver):
            # Pinned pages are committed state: rehydrate only the messages
            # channel instead of loading files and every other graph channel.
            messages = await load_conversation_messages(
                graph.checkpointer, conversation_id, checkpoint_id=checkpoint_id
            )
            if messages is None:
                return _json_error("Conversation not found", "not_found", 404)
            summaries = ()
        else:
            config: dict[str, Any] = {"configurable": {"thread_id": conversation_id}}
            if checkpoint_id:
                config["configurable"]["checkpoint_id"] = checkpoint_id
            snapshot = await graph.aget_state(config)
            if snapshot.created_at is None:
                return _json_error("Conversation not found", "not_found", 404)
            messages = snapshot.values.get("messages") or []
            summaries = ()

        sourced_items = conversation_items_with_sources(messages, conversation_id)
        try:
            page, meta = paginate_items(
                [item for _, item in sourced_items], after=after, limit=limit, order=order
            )
        except ValueError as exc:
            return _json_error(str(exc), "bad_request", 400, param="after")
        payload: dict[str, Any] = {**meta, "data": page}
        if include_langchain:
            payload[CONVERSATION_MESSAGES_FIELD] = messages_to_dict(messages)
        if include_summaries:
            payload[CONVERSATION_SUMMARIES_FIELD] = [
                _summary_occurrence_payload(summary) for summary in summaries
            ]
        return web.json_response(make_json_safe(payload))

    # -- DELETE /v1/conversations/{conversation_id} ----------------------

    async def handle_delete(self, request: web.Request) -> web.Response:
        conversation_id = request.match_info["conversation_id"]
        if self._conversation_gate.is_active(conversation_id):
            return _json_error("Conversation has an in-flight request", "conflict", 409)

        graph = getattr(self.loop_runner, "graph", None)
        if graph is None:
            return _json_error("Graph is not available", "graph_unavailable", 503)

        try:
            await graph.checkpointer.adelete_thread(conversation_id)
            # Everything else the runtime keeps per conversation goes too, so a
            # deleted conversation leaves no live state behind to resurface.
            context = getattr(self.loop_runner, "heartbeat_context", None)
            if context is not None:
                await context.state_manager.delete_current_state(conversation_id)
            if self._chat_queue is not None:
                await self._chat_queue.delete(conversation_id)
        except Exception as exc:
            logger.warning("Deleting conversation %s failed: %s", conversation_id, exc)
            return _json_error(f"Failed to delete conversation: {exc}", "server_error", 500)

        return web.json_response(
            {
                "id": conversation_id,
                "object": "conversation.deleted",
                "deleted": True,
            }
        )

    # -- GET /v1/events --------------------------------------------------

    async def handle_events(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
        )
        await response.prepare(request)

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=EVENTS_SUBSCRIBER_QUEUE_SIZE
        )
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=EVENTS_KEEPALIVE_SECONDS)
                except TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                if item is None:
                    break
                await self._write_frame(response, "agent_event", item)
        except (ConnectionResetError, ConnectionError):
            pass
        finally:
            self._subscribers.discard(queue)
        return response

    async def _drain_loop(self) -> None:
        queues = getattr(self.loop_runner, "queues", None)
        if queues is None:
            return
        while True:
            try:
                event = await queues.get_event(timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error draining runtime event queue")
                await asyncio.sleep(1.0)
                continue
            if event is None:
                continue
            if not self._subscribers:
                logger.debug(
                    "No event subscribers connected, dropping event: %s", event.get("event_type")
                )
                continue
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "Event subscriber queue full, dropping event: %s", event.get("event_type")
                    )

    # -- shared helpers --------------------------------------------------

    def _can_run_deepagent_direct(self) -> bool:
        graph = getattr(self.loop_runner, "graph", None)
        return callable(getattr(graph, "astream", None))

    def _create_direct_stream_bridge(self) -> DeepAgentSSEStream:
        return self._bridge_factory(
            self.loop_runner.graph,
            config=self.loop_runner.config,
            state={},
        )

    async def _complete_direct_graph_cycle(
        self, bridge: DeepAgentSSEStream, thread_id: str
    ) -> None:
        context = getattr(self.loop_runner, "heartbeat_context", None)
        if context is not None:
            thread_state = await context.state_manager.get_current_state(thread_id=thread_id)
            turn_state = {
                **thread_state,
                "thread_id": thread_id,
                **bridge.state,
            }
            updates = await checkpoint_node(
                cast(ProgenaState, turn_state),
                Runtime(context=context),
            )
            await context.state_manager.update_current_state(
                {
                    k: v
                    for k, v in updates.items()
                    if k in {"cycle_count", "pending_response", "last_reasoning_trace"}
                },
                thread_id=thread_id,
            )

    async def _notify_queued_request(self, request_id: str, *, streaming: bool) -> None:
        queues = getattr(self.loop_runner, "queues", None)
        if queues is None:
            return
        logger.info("Streaming request %s queued behind active request", request_id)
        await queues.push_event(
            "message_queued",
            "A previous message is still being processed; this message will run next.",
            {"request_id": request_id, "streaming": streaming},
        )

    def _ensure_content_stream(self, response_id: str) -> _ResponseContentStream:
        stream = self._content_streams.get(response_id)
        if stream is not None:
            return stream
        now = time.monotonic()
        for stream_id, candidate in tuple(self._content_streams.items()):
            if (
                candidate.terminal
                and candidate.finished_at is not None
                and now - candidate.finished_at >= _RESPONSE_STREAM_RETENTION_SECONDS
            ):
                self._content_streams.pop(stream_id, None)
        stream = _ResponseContentStream()
        self._content_streams[response_id] = stream
        while len(self._content_streams) > _RESPONSE_STREAM_REPLAY_LIMIT:
            oldest_id, oldest = next(iter(self._content_streams.items()))
            if not oldest.terminal:
                break
            self._content_streams.pop(oldest_id)
        return stream

    def _activate_root(
        self, conversation_id: str, response_id: str, cancel_event: asyncio.Event
    ) -> None:
        self._active_root_executions[conversation_id] = _ActiveRootExecution(
            response_id=response_id, cancel_event=cancel_event
        )

    def _deactivate_root(
        self, conversation_id: str, response_id: str, cancel_event: asyncio.Event
    ) -> None:
        active = self._active_root_executions.get(conversation_id)
        if (
            active is not None
            and active.response_id == response_id
            and active.cancel_event is cancel_event
        ):
            self._active_root_executions.pop(conversation_id, None)

    def _cancel_captured_root(self, conversation_id: str, captured: _ActiveRootExecution) -> None:
        active = self._active_root_executions.get(conversation_id)
        if active is not None and active is captured:
            active.cancel_event.set()

    @staticmethod
    async def _write_frame(
        response: web.StreamResponse | _ResponseContentSink,
        event: str,
        data: dict[str, Any],
    ) -> None:
        if isinstance(response, _ResponseContentSink):
            if not response.defer_terminal(event, data):
                response.stream.append(event, data)
            return
        payload = json.dumps(make_json_safe(data)).encode("utf-8")
        await response.write(f"event: {event}\n".encode() + b"data: " + payload + b"\n\n")

    @staticmethod
    async def _write_replay_frame(
        response: web.StreamResponse, cursor: int, event: str, data: dict[str, Any]
    ) -> None:
        payload = json.dumps(make_json_safe(data)).encode("utf-8")
        await response.write(
            f"id: {cursor}\nevent: {event}\n".encode() + b"data: " + payload + b"\n\n"
        )

    async def _write_failed(
        self,
        response: web.StreamResponse | _ResponseContentSink,
        response_id: str,
        thread_id: str,
        model: str,
        metadata: dict[str, Any] | None,
        sequence: int,
        code: str,
        exc: BaseException,
    ) -> None:
        resp_obj = _failed_response(response_id, thread_id, model, metadata, code, exc)
        with contextlib.suppress(ConnectionResetError, ConnectionError):
            await self._write_frame(
                response,
                "response.failed",
                build_sse_event("response.failed", {"response": resp_obj}, sequence),
            )
