"""HTTP + SSE endpoints for the OpenAI Responses/Conversations API contract.

Served from the runtime's persistent HTTP server; every endpoint is backed by
the native LangGraph agent graph (``astream``, ``aget_state``, checkpointer).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar, cast

from aiohttp import web
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from ..nodes.checkpoint import checkpoint_node
from ..runtime.responses_api import (
    CANCELLED_STATUS,
    build_response_object,
    build_sse_event,
    conversation_items_with_sources,
    error_response,
    paginate_items,
    with_langchain_messages,
)
from ..runtime.thread_context import reset_current_thread_id, set_current_thread_id
from ..sessions.render import merge_session_after_turn
from ..sse.deepagent_stream import DeepAgentSSEStream
from ..sse.stream_payloads import make_json_safe
from ..state.models import ProgenitorState

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

EVENTS_KEEPALIVE_SECONDS = 15.0
EVENTS_SUBSCRIBER_QUEUE_SIZE = 256
DIRECT_STREAM_QUEUE_SIZE = 256
# The deepagent (langchain create_agent) node that produces assistant messages;
# a cancelled turn's partial reply is written as if this node returned it.
MODEL_NODE = "model"

INPUT_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
MAX_INPUT_IMAGE_BYTES = int(os.getenv("PROGENITOR_MAX_INPUT_IMAGE_BYTES", str(5 * 1024 * 1024)))

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


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


def _input_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """LangChain ``image_url`` block for a Responses ``input_image`` part.

    Only inline ``data:`` URLs of common image types are accepted, so the
    runtime never fetches remote URLs on a caller's behalf.
    """
    image_url = block.get("image_url")
    url = image_url.get("url") if isinstance(image_url, dict) else image_url
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ValueError("input_image must be a base64 data: URL")
    header, sep, data = url.partition(",")
    mime_type = header[len("data:") :].split(";", 1)[0].lower()
    if not sep or ";base64" not in header:
        raise ValueError("input_image must be a base64 data: URL")
    if mime_type not in INPUT_IMAGE_MIME_TYPES:
        raise ValueError(f"Unsupported input_image type: {mime_type or 'unknown'}")
    try:
        size = len(base64.b64decode(data, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("input_image has invalid base64 data") from exc
    if size > MAX_INPUT_IMAGE_BYTES:
        raise ValueError(
            f"input_image is {size:,} bytes; the limit is {MAX_INPUT_IMAGE_BYTES:,} bytes"
        )
    return {"type": "image_url", "image_url": {"url": url}}


def _extract_input(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """User text and image blocks from the ``input`` field (string or list of items).

    Raises ``ValueError`` for an ``input_image`` part that cannot be accepted.
    """
    raw = payload.get("input")
    if isinstance(raw, str):
        return raw.strip(), []
    parts: list[str] = []
    images: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("role") == "user":
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content.strip())
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "input_text":
                            parts.append(str(block.get("text", "")).strip())
                        elif block.get("type") == "input_image":
                            images.append(_input_image_block(block))
    return "\n\n".join(parts), images


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
        self._inflight_by_thread: dict[str, asyncio.Task[Any]] = {}
        self._session_renders: dict[str, asyncio.Task[None]] = {}
        self._session_renders_pending: set[str] = set()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._drain_task: asyncio.Task[None] | None = None

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/v1/responses", self.handle_responses),
            web.post("/v1/responses/{response_id}/cancel", self.handle_cancel),
            web.get("/v1/conversations/{conversation_id}/items", self.handle_items),
            web.delete("/v1/conversations/{conversation_id}", self.handle_delete),
            web.get("/v1/events", self.handle_events),
        ]

    async def start(self) -> None:
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_loop())

    async def stop(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None
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

        try:
            message, images = _extract_input(payload)
        except ValueError as exc:
            return _json_error(str(exc), "invalid_input_image", 400, param="input")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
        attachments = _extract_attachments(metadata)

        if not message and not attachments and not images:
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
        model = str(payload.get("model") or "progenitor-v1")
        stream = bool(payload.get("stream", False))
        thread_id = _conversation_id(payload.get("conversation"))

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
                        images=images,
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
                    images=images,
                )

            await self._run_serialized(thread_id, _turn, on_queued=_on_queued)
            return response
        finally:
            self._cancel_events.pop(response_id, None)

    async def _run_serialized(
        self,
        thread_id: str,
        turn: Callable[[], Coroutine[Any, Any, _T]],
        *,
        on_queued: Callable[[], Awaitable[None]],
    ) -> _T:
        """Run one turn after any in-flight turn on the same conversation.

        Serializing per conversation keeps two concurrent turns on one thread
        from creating divergent checkpoint branches; different conversations
        run fully concurrently. The running turn is registered so
        ``DELETE /v1/conversations/{id}`` can refuse while it is in flight.
        """
        existing = self._inflight_by_thread.get(thread_id)
        if existing is not None and not existing.done():
            await on_queued()
            with contextlib.suppress(Exception):
                await existing
        task = asyncio.create_task(turn())
        self._inflight_by_thread[thread_id] = task
        try:
            return await task
        finally:
            if self._inflight_by_thread.get(thread_id) is task:
                self._inflight_by_thread.pop(thread_id, None)

    async def _stream_turn(
        self,
        response: web.StreamResponse,
        message: str,
        response_id: str,
        thread_id: str,
        model: str,
        metadata: dict[str, Any] | None,
        cancel_event: asyncio.Event,
        received_at: float,
        start_sequence: int,
        source: str | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> None:
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
            await self._write_failed(
                response, response_id, thread_id, model, metadata, seq, "state_error", exc
            )
            return

        token = set_current_thread_id(thread_id)
        text_item_opened = False
        current_text = ""
        item_id = f"msg_{response_id}"
        output_index = 0
        content_index = 0

        try:

            async def _produce() -> None:
                try:
                    async for item in bridge.stream(
                        message,
                        thread_id=thread_id,
                        received_at=received_at,
                        attachments=_extract_attachments(metadata),
                        source=source,
                        images=images,
                    ):
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

                    try:
                        kind, payload = await asyncio.wait_for(
                            queue.get(), timeout=EVENTS_KEEPALIVE_SECONDS
                        )
                    except TimeoutError:
                        await response.write(b": keepalive\n\n")
                        continue

                    if cancel_event.is_set():
                        cancelled = True
                        break

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
                        return

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
                            "progenitor.stream_event",
                            build_sse_event("progenitor.stream_event", event_payload, seq),
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
                return
            finally:
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

            if bridge_cancelled and not cancel_event.is_set():
                logger.info("Direct DeepAgent bridge cancelled for %s", response_id)
                return

            if cancelled or cancel_event.is_set():
                logger.info("Direct DeepAgent stream cancelled for %s", response_id)
                output = await self._persist_cancelled_turn(
                    thread_id, response_id, current_text, before_count
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
                return

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
                await self._write_failed(
                    response, response_id, thread_id, model, metadata, seq, "checkpoint_error", exc
                )
                return

            resp_obj = build_response_object(
                response_id, thread_id, model, "completed", output, metadata
            )
            with contextlib.suppress(ConnectionResetError, ConnectionError):
                await self._write_frame(
                    response,
                    "response.completed",
                    build_sse_event("response.completed", {"response": resp_obj}, seq),
                )
        finally:
            reset_current_thread_id(token)
            self._fire_and_forget_session_merge(thread_id)

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
        images: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        bridge = self._create_direct_stream_bridge()
        try:
            before_count = await self._message_count(thread_id)
        except Exception as exc:
            logger.error("State read error for %s: %s", response_id, exc, exc_info=True)
            return _failed_response(response_id, thread_id, model, metadata, "state_error", exc)

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
                    images=images,
                )
                # aclosing: a cancel must stop the graph run before the
                # cancelled reply is written to the thread.
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
                resp_obj = build_response_object(
                    response_id, thread_id, model, "failed", [], metadata
                )
                resp_obj["error"] = {"code": "stream_error", "message": str(exc)}
                return resp_obj

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
            self._fire_and_forget_session_merge(thread_id)

    async def _persist_cancelled_turn(
        self, thread_id: str, response_id: str, partial_text: str, before_count: int
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

        include_langchain = "progenitor.langchain_message" in include_set

        checkpoint_id = request.query.get("checkpoint_id") or None
        config: dict[str, Any] = {"configurable": {"thread_id": conversation_id}}
        if checkpoint_id:
            config["configurable"]["checkpoint_id"] = checkpoint_id

        snapshot = await graph.aget_state(config)
        if snapshot.created_at is None:
            return _json_error("Conversation not found", "not_found", 404)

        messages = snapshot.values.get("messages") or []
        sourced_items = conversation_items_with_sources(messages, conversation_id)
        try:
            page, meta = paginate_items(
                [item for _, item in sourced_items], after=after, limit=limit, order=order
            )
        except ValueError as exc:
            return _json_error(str(exc), "bad_request", 400, param="after")
        if include_langchain:
            page = with_langchain_messages(
                page,
                {item["id"]: index for index, item in sourced_items},
                messages,
                conversation_id,
            )
        return web.json_response(make_json_safe({**meta, "data": page}))

    # -- DELETE /v1/conversations/{conversation_id} ----------------------

    async def handle_delete(self, request: web.Request) -> web.Response:
        conversation_id = request.match_info["conversation_id"]
        existing = self._inflight_by_thread.get(conversation_id)
        if existing is not None and not existing.done():
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
                cast(ProgenitorState, turn_state),
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

    @staticmethod
    async def _write_frame(response: web.StreamResponse, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(make_json_safe(data)).encode("utf-8")
        await response.write(f"event: {event}\n".encode() + b"data: " + payload + b"\n\n")

    async def _write_failed(
        self,
        response: web.StreamResponse,
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
