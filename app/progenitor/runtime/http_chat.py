"""HTTP + SSE chat endpoints served from the runtime's persistent HTTP server.

Replaces the TCP IPC server's direct-DeepAgent request path. Only that path is
ported here (the legacy queue-drain path in the old IPC server was dead code).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any, cast

from aiohttp import web
from langgraph.runtime import Runtime

from ..ipc.deepagent_stream import DeepAgentIPCStream
from ..ipc.stream_payloads import make_json_safe
from ..nodes.checkpoint import checkpoint_node
from ..state.models import ProgenitorState

logger = logging.getLogger(__name__)

EVENTS_KEEPALIVE_SECONDS = 15.0
EVENTS_SUBSCRIBER_QUEUE_SIZE = 256

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _error(message: str, code: str, status: int) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=status)


class RuntimeChatService:
    """Owns the runtime's chat request/cancel/event-broadcast HTTP endpoints."""

    def __init__(self, loop_runner: Any, *, bridge_factory: Any = None) -> None:
        self.loop_runner = loop_runner
        self._bridge_factory = bridge_factory or DeepAgentIPCStream
        self._request_lock = asyncio.Lock()
        self._chat_waiters = 0
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

        if not self._can_run_deepagent_direct():
            return _error("DeepAgent graph is not available", "GRAPH_UNAVAILABLE", 503)

        request_id = str(payload.get("request_id") or "").strip() or f"req_{uuid.uuid4().hex[:12]}"
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", **_SSE_HEADERS},
        )
        await response.prepare(request)

        self._chat_waiters += 1
        try:
            if self._chat_waiters > 1:
                await self._write_frame(
                    response, "queued", {"request_id": request_id, "streaming": True}
                )
                await self._notify_queued_request(request_id, streaming=True)

            cancel_event = self._cancel_events.setdefault(request_id, asyncio.Event())
            try:
                async with self._request_lock:
                    await self._stream_chat(response, message, request_id, metadata, cancel_event)
            finally:
                self._cancel_events.pop(request_id, None)
        finally:
            self._chat_waiters -= 1
        return response

    async def _stream_chat(
        self,
        response: web.StreamResponse,
        message: str,
        request_id: str,
        metadata: dict[str, Any] | None,
        cancel_event: asyncio.Event,
    ) -> None:
        thread_id = self._thread_id_for_request(metadata)
        async with self._direct_graph_lock():
            bridge = self._create_direct_stream_bridge()
            cancelled = False
            try:
                async for item in bridge.stream(message, thread_id=thread_id):
                    if cancel_event.is_set():
                        cancelled = True
                        break

                    item_type = str(item.get("type", "stream_chunk"))
                    if item_type == "stream_event":
                        event_payload: dict[str, Any] = {
                            "type": "stream_event",
                            "event_type": str(item.get("event_type", "unknown")),
                            "content": str(item.get("content", "")),
                            "metadata": item.get("metadata"),
                            "request_id": request_id,
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

            if cancelled or cancel_event.is_set():
                logger.info("Direct DeepAgent stream cancelled for %s", request_id)
                with contextlib.suppress(ConnectionResetError, ConnectionError):
                    await self._write_frame(
                        response, "done", {"request_id": request_id, "status": "cancelled"}
                    )
                return

            try:
                await self._complete_direct_graph_cycle(bridge)
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
        state = getattr(self.loop_runner, "state", {})
        requested_thread_id = str((metadata or {}).get("thread_id") or "").strip()
        return requested_thread_id or str(state.get("thread_id") or "progenitor_main")

    def _create_direct_stream_bridge(self) -> DeepAgentIPCStream:
        return self._bridge_factory(
            self.loop_runner.graph,
            config=self.loop_runner.config,
            state=getattr(self.loop_runner, "state", {}),
        )

    @contextlib.asynccontextmanager
    async def _direct_graph_lock(self) -> Any:
        lock = getattr(self.loop_runner, "graph_lock", None)
        if lock is None:
            yield
        else:
            async with lock:
                yield

    async def _complete_direct_graph_cycle(self, bridge: DeepAgentIPCStream) -> None:
        state = getattr(self.loop_runner, "state", {})
        context = getattr(self.loop_runner, "heartbeat_context", None)
        if context is not None:
            updates = await checkpoint_node(
                cast(ProgenitorState, state),
                Runtime(context=context),
            )
            if isinstance(state, dict):
                state.update(updates)

        queues = getattr(self.loop_runner, "queues", None)
        if queues is not None:
            queues.signal_cycle_complete()

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
