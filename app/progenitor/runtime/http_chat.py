"""HTTP + SSE chat endpoints served from the runtime's persistent HTTP server.

Replaces the TCP IPC server's direct-DeepAgent request path. Only that path is
ported here (the legacy queue-drain path in the old IPC server was dead code).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any, cast

from aiohttp import web
from langgraph.runtime import Runtime

from ..ipc.deepagent_stream import DeepAgentIPCStream
from ..ipc.stream_payloads import make_json_safe
from ..nodes.checkpoint import checkpoint_node
from ..runtime.thread_context import reset_current_thread_id, set_current_thread_id
from ..state.models import ProgenitorState

logger = logging.getLogger(__name__)

EVENTS_KEEPALIVE_SECONDS = 15.0
EVENTS_SUBSCRIBER_QUEUE_SIZE = 256
DIRECT_STREAM_QUEUE_SIZE = 256

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _error(message: str, code: str, status: int) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=status)


def _extract_attachments(metadata: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Pull the resolved attachment list the backend stamps onto
    ``request_metadata["attachments"]`` (see
    ``app.backend.main._resolve_message_attachments``) back out of the chat
    request's metadata, so it can ride onto the HumanMessage's
    ``additional_kwargs`` for chat-history display (see
    ``DeepAgentIPCStream.stream``)."""
    if not isinstance(metadata, dict):
        return None
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return None
    return [item for item in attachments if isinstance(item, dict)] or None


class RuntimeChatService:
    """Owns the runtime's chat request/cancel/event-broadcast HTTP endpoints."""

    def __init__(self, loop_runner: Any, *, bridge_factory: Any = None) -> None:
        self.loop_runner = loop_runner
        self._bridge_factory = bridge_factory or DeepAgentIPCStream
        self._inflight_by_thread: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._drain_task: asyncio.Task[None] | None = None

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/v1/chat", self.handle_chat),
            web.post("/v1/chat/{request_id}/cancel", self.handle_cancel),
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

    # -- POST /v1/chat --------------------------------------------------

    async def handle_chat(self, request: web.Request) -> web.StreamResponse | web.Response:
        try:
            payload = await request.json()
        except Exception:
            return _error("Invalid JSON body", "BAD_REQUEST", 400)
        if not isinstance(payload, dict):
            return _error("Expected a JSON object body", "BAD_REQUEST", 400)

        message = str(payload.get("message") or "").strip()
        if not message:
            return _error("message is required", "BAD_REQUEST", 400)
        # Stamped now, before any queueing delay below, so the timestamp
        # reflects when the user actually sent the message rather than
        # whenever the graph gets around to processing it.
        received_at = time.time()

        if not self._can_run_deepagent_direct():
            return _error("DeepAgent graph is not available", "GRAPH_UNAVAILABLE", 503)

        request_id = str(payload.get("request_id") or "").strip() or f"req_{uuid.uuid4().hex[:12]}"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
        )
        await response.prepare(request)

        thread_id = self._thread_id_for_request(metadata)
        cancel_event = self._cancel_events.setdefault(request_id, asyncio.Event())
        try:
            # Serialize requests for the SAME thread_id so two concurrent
            # ainvoke() calls on the same thread don't create divergent
            # checkpoint branches.  Different threads run fully concurrently.
            existing = self._inflight_by_thread.get(thread_id)
            if existing is not None and not existing.done():
                await self._write_frame(
                    response, "queued", {"request_id": request_id, "streaming": True}
                )
                await self._notify_queued_request(request_id, streaming=True)
                with contextlib.suppress(Exception):
                    await existing

            task = asyncio.create_task(
                self._stream_chat(
                    response, message, request_id, metadata, cancel_event, received_at
                )
            )
            self._inflight_by_thread[thread_id] = task
            try:
                await task
            finally:
                if self._inflight_by_thread.get(thread_id) is task:
                    self._inflight_by_thread.pop(thread_id, None)
        finally:
            self._cancel_events.pop(request_id, None)
        return response

    async def _stream_chat(
        self,
        response: web.StreamResponse,
        message: str,
        request_id: str,
        metadata: dict[str, Any] | None,
        cancel_event: asyncio.Event,
        received_at: float,
    ) -> None:
        thread_id = self._thread_id_for_request(metadata)
        bridge = self._create_direct_stream_bridge()
        cancelled = False
        bridge_cancelled = False

        # bridge.stream() can go silent for minutes at a time (a long tool
        # call or subagent invocation with no interim output), which would
        # otherwise starve the response body of bytes and trip the
        # backend's httpx read timeout. Consume it from a background task
        # so this loop can keep writing SSE keepalive comments while
        # waiting, the same pattern handle_events() uses.
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=DIRECT_STREAM_QUEUE_SIZE)

        token = set_current_thread_id(thread_id)
        try:

            async def _produce() -> None:
                try:
                    async for item in bridge.stream(
                        message,
                        thread_id=thread_id,
                        received_at=received_at,
                        attachments=_extract_attachments(metadata),
                    ):
                        await queue.put(("item", item))
                except asyncio.CancelledError:
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(("cancelled", None))
                    raise
                except Exception as exc:  # noqa: BLE001 - forwarded to the consumer below
                    await queue.put(("error", exc))
                else:
                    await queue.put(("done", None))

            producer = asyncio.create_task(_produce())
            try:
                stream_sequence = 0
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
                        logger.error("Streaming error for %s: %s", request_id, exc, exc_info=exc)
                        with contextlib.suppress(ConnectionResetError, ConnectionError):
                            await self._write_frame(
                                response,
                                "error",
                                {
                                    "request_id": request_id,
                                    "error": str(exc),
                                    "code": "STREAM_ERROR",
                                },
                            )
                        return

                    item = payload
                    stream_sequence += 1
                    runtime_emitted_at_ms = int(time.time() * 1000)
                    item_type = str(item.get("type", "stream_chunk"))
                    if item_type == "stream_event":
                        event_payload: dict[str, Any] = {
                            "type": "stream_event",
                            "event_type": str(item.get("event_type", "unknown")),
                            "content": str(item.get("content", "")),
                            "metadata": item.get("metadata"),
                            "request_id": request_id,
                            "runtime_emitted_at_ms": runtime_emitted_at_ms,
                            "runtime_stream_seq": stream_sequence,
                        }
                        images = item.get("images")
                        if isinstance(images, list) and images:
                            event_payload["images"] = images
                        await self._write_frame(response, "stream_event", event_payload)
                        continue

                    is_final = bool(item.get("is_final"))
                    await self._write_frame(
                        response,
                        "stream_chunk",
                        {
                            "type": "stream_chunk",
                            "content": str(item.get("content", "")),
                            "is_final": is_final,
                            "request_id": request_id,
                            "runtime_emitted_at_ms": runtime_emitted_at_ms,
                            "runtime_stream_seq": stream_sequence,
                        },
                    )
                    if is_final:
                        break
            except (ConnectionResetError, ConnectionError):
                cancelled = True
            except Exception as exc:
                logger.error("Streaming error for %s: %s", request_id, exc, exc_info=True)
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response,
                        "error",
                        {"request_id": request_id, "error": str(exc), "code": "STREAM_ERROR"},
                    )
                return
            finally:
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

            if bridge_cancelled and not cancel_event.is_set():
                logger.info("Direct DeepAgent bridge cancelled for %s", request_id)
                return

            if cancelled or cancel_event.is_set():
                logger.info("Direct DeepAgent stream cancelled for %s", request_id)
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response, "done", {"request_id": request_id, "status": "cancelled"}
                    )
                return

            try:
                await self._complete_direct_graph_cycle(bridge, thread_id)
            except Exception as exc:
                logger.error("Checkpoint error for %s: %s", request_id, exc, exc_info=True)
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response,
                        "error",
                        {"request_id": request_id, "error": str(exc), "code": "CHECKPOINT_ERROR"},
                    )
                return

            await self._write_frame(
                response, "done", {"request_id": request_id, "status": "completed"}
            )
        finally:
            reset_current_thread_id(token)

    # -- POST /v1/chat/{request_id}/cancel -------------------------------

    async def handle_cancel(self, request: web.Request) -> web.Response:
        request_id = request.match_info["request_id"]
        cancel_event = self._cancel_events.get(request_id)
        if cancel_event is not None:
            cancel_event.set()
            return web.json_response({"request_id": request_id, "cancelled": True})
        return web.json_response({"request_id": request_id, "cancelled": False})

    # -- GET /v1/events ---------------------------------------------------

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

    # -- shared helpers, ported from the direct DeepAgent path in ipc/server.py --

    def _can_run_deepagent_direct(self) -> bool:
        graph = getattr(self.loop_runner, "graph", None)
        return callable(getattr(graph, "astream", None))

    def _thread_id_for_request(self, metadata: dict[str, Any] | None) -> str:
        system_state = getattr(self.loop_runner, "state", {})
        requested_thread_id = str((metadata or {}).get("thread_id") or "").strip()
        return requested_thread_id or str(system_state.get("thread_id") or "progenitor_main")

    def _create_direct_stream_bridge(self) -> DeepAgentIPCStream:
        # Fresh empty dict per request — DeepAgentIPCStream uses this only for
        # transient per-turn fields (current_input, cycle_count, etc.) and
        # never reads it back across turns.
        return self._bridge_factory(
            self.loop_runner.graph,
            config=self.loop_runner.config,
            state={},
        )

    async def _complete_direct_graph_cycle(
        self, bridge: DeepAgentIPCStream, thread_id: str
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
