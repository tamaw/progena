"""
HTTP-based Progenitor Client for Backend.

Talks to the Progenitor runtime's persistent HTTP + SSE endpoints
(``POST /v1/chat``, ``POST /v1/chat/{request_id}/cancel``, ``GET /v1/events``)
rather than the old TCP socket IPC protocol. The class name and public API are
unchanged so callers (``main.py``, ``ui_routes.py``) and tests didn't need to
change when the transport moved.

Features:
- HTTP/SSE communication (no custom framing)
- Native streaming support
- Automatic event-stream reconnection
- Event forwarding from agent to frontend via SSE
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import quote

import httpx

from .config import ChatServerConfig
from .models import AgentResponse

logger = logging.getLogger(__name__)

# Type for event callback (will be ui_routes.broadcast_chat_event)
EventCallback = Callable[[str, dict], None]

HEALTH_PROBE_ATTEMPTS = 10
HEALTH_PROBE_RETRY_DELAY_SECONDS = 1.0
HEALTH_POLL_INTERVAL_SECONDS = 10.0
HEARTBEAT_STALE_SECONDS = 30.0
EVENTS_RECONNECT_DELAY_SECONDS = 5.0
CANCEL_TIMEOUT_SECONDS = 5.0
TASK_SHUTDOWN_TIMEOUT_SECONDS = 5.0


async def _iter_sse_frames(response: httpx.Response) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse ``event:``/``data:`` SSE frames from a streaming httpx response."""
    event_name: str | None = None
    async for line in response.aiter_lines():
        if line == "" or line.startswith(":"):
            continue
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
            continue
        if line.startswith("data: "):
            if event_name is None:
                continue
            yield event_name, json.loads(line[len("data: ") :])
            event_name = None


class IPCProgenitorClient:
    """Client that communicates with Progenitor via HTTP + SSE.

    This is the recommended client for production use, talking to the
    runtime's persistent HTTP server for chat requests, cancellation, and
    background event forwarding to the frontend via SSE.
    """

    def __init__(self, config: ChatServerConfig, event_callback: EventCallback | None = None):
        """Initialize the HTTP-based client.

        Args:
            config: Backend configuration.
            event_callback: Optional callback for forwarding events to SSE.
                           Signature: (event_type: str, data: dict) -> None
        """
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._started = False
        self._lock = asyncio.Lock()
        self._event_callback = event_callback
        self._last_heartbeat: float | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._events_task: asyncio.Task[None] | None = None

    @property
    def _base_url(self) -> str:
        return self.config.progenitor_runtime_url

    async def start(self) -> None:
        """Start the client, waiting for the runtime HTTP server to be reachable."""
        if self._started:
            return

        async with self._lock:
            if self._started:
                return

            client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(5.0, read=None),
            )

            connected = False
            for attempt in range(HEALTH_PROBE_ATTEMPTS):
                try:
                    response = await client.get("/health")
                    if response.status_code == 200:
                        connected = True
                        break
                except Exception as e:
                    logger.debug("Health probe attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(HEALTH_PROBE_RETRY_DELAY_SECONDS)

            if not connected:
                await client.aclose()
                raise RuntimeError(
                    f"Failed to reach Progenitor runtime HTTP server at {self._base_url}"
                )

            self._client = client
            self._last_heartbeat = time.time()
            self._health_task = asyncio.create_task(self._health_poll_loop())
            self._events_task = asyncio.create_task(self._events_loop())

            self._started = True
            logger.info("HTTP Progenitor client started (%s)", self._base_url)

    async def stop(self) -> None:
        """Stop the client and its background tasks."""
        async with self._lock:
            for task in (self._health_task, self._events_task):
                if task is not None:
                    task.cancel()
            for task in (self._health_task, self._events_task):
                if task is None:
                    continue
                # A cancelled task blocked deep in an SSE read can be slow to
                # unwind; never let shutdown hang waiting for it.
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(task, timeout=TASK_SHUTDOWN_TIMEOUT_SECONDS)
            self._health_task = None
            self._events_task = None

            if self._client is not None:
                await self._client.aclose()
                self._client = None

            self._started = False

    async def _health_poll_loop(self) -> None:
        """Periodically probe ``/health`` so ``is_running`` reflects liveness."""
        assert self._client is not None
        while True:
            try:
                await asyncio.sleep(HEALTH_POLL_INTERVAL_SECONDS)
                response = await self._client.get("/health")
                if response.status_code == 200:
                    self._last_heartbeat = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Health poll failed: %s", e)

    async def _events_loop(self) -> None:
        """Consume the runtime's broadcast SSE stream and forward events."""
        assert self._client is not None
        while True:
            try:
                async with self._client.stream("GET", "/v1/events") as response:
                    response.raise_for_status()
                    async for event_name, data in _iter_sse_frames(response):
                        if event_name != "agent_event":
                            continue
                        self._last_heartbeat = time.time()
                        self._forward_event(data)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug("Events stream disconnected, reconnecting: %s", e)
                await asyncio.sleep(EVENTS_RECONNECT_DELAY_SECONDS)

    def _forward_event(self, event: dict[str, Any]) -> None:
        """Forward a runtime broadcast event to the SSE callback, if set."""
        if self._event_callback is None:
            return

        event_type = str(event.get("event_type", "unknown"))
        content = event.get("content", "")
        logger.info(
            "Forwarding event to SSE: type=%s, content=%s",
            event_type,
            str(content)[:100] if content else "",
        )

        payload: dict[str, Any] = {
            "content": content,
            "metadata": event.get("metadata", {}),
            "timestamp": event.get("timestamp"),
        }
        images = event.get("images")
        if isinstance(images, list) and images:
            payload["images"] = images

        try:
            self._event_callback(event_type, payload)
        except Exception as e:
            logger.warning("Error in event callback: %s", e)

    async def send_message(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        include_reasoning: bool | None = None,
    ) -> AgentResponse:
        """Send a message and get the normalized response model.

        Args:
            message: The user message.
            request_id: Optional request ID for correlation.

        Returns:
            The normalized agent response.
        """
        if not self._started:
            await self.start()

        chunks: list[str] = []
        effective_include_reasoning = (
            self.config.reasoning_enabled if include_reasoning is None else include_reasoning
        )
        reasoning_text: str | None = None
        reasoning_tokens: int | None = None
        async for event in self.send_message_stream_events(message, request_id, metadata):
            event_type = str(event.get("type", ""))
            if event_type == "stream_event":
                stream_event_type = str(event.get("event_type", ""))
                event_content = str(event.get("content", ""))
                if stream_event_type == "reasoning_delta" and event_content:
                    reasoning_text = f"{reasoning_text or ''}{event_content}"
                elif stream_event_type == "reasoning_summary" and event_content:
                    prior_text = reasoning_text or ""
                    reasoning_text = (
                        event_content if len(event_content) >= len(prior_text) else prior_text
                    )
                event_metadata = event.get("metadata")
                if isinstance(event_metadata, dict):
                    raw_tokens = event_metadata.get("reasoning_tokens")
                    if isinstance(raw_tokens, int):
                        reasoning_tokens = raw_tokens
                continue

            if event_type != "stream_chunk":
                continue
            chunk_text = str(event.get("content", ""))
            if chunk_text:
                chunks.append(chunk_text)
        response_text = "".join(chunks)
        return AgentResponse(
            response=response_text,
            reasoning=reasoning_text if effective_include_reasoning else None,
            reasoning_tokens=reasoning_tokens if effective_include_reasoning else None,
        )

    async def send_message_stream_events(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a message and stream structured chat events."""
        if not self._started:
            await self.start()
        assert self._client is not None

        body: dict[str, Any] = {"message": message}
        if request_id:
            body["request_id"] = request_id
        if metadata:
            body["metadata"] = metadata

        async with self._client.stream("POST", "/v1/chat", json=body) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(f"Progenitor error: {_decode_error_body(error_body)}")

            async for event_name, data in _iter_sse_frames(response):
                if event_name == "queued":
                    continue
                if event_name == "error":
                    raise RuntimeError(f"Progenitor error: {data.get('error', 'unknown error')}")
                if event_name == "done":
                    return
                if event_name in ("stream_chunk", "stream_event"):
                    yield data

    async def cancel_request(self, request_id: str) -> bool:
        """Best-effort cancellation of an active chat request."""
        if not request_id.strip():
            return False

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(CANCEL_TIMEOUT_SECONDS)
            ) as client:
                response = await client.post(f"/v1/chat/{quote(request_id, safe='')}/cancel")
                response.raise_for_status()
                return bool(response.json().get("cancelled", False))
        except Exception as e:
            logger.warning("Failed to send HTTP cancel for %s: %s", request_id, e)
            return False

    @property
    def is_running(self) -> bool:
        """Check if the client is running and recently heard from the runtime."""
        if not self._started or self._last_heartbeat is None:
            return False
        return (time.time() - self._last_heartbeat) < HEARTBEAT_STALE_SECONDS

    def last_heartbeat(self) -> float | None:
        """Get last heartbeat timestamp."""
        return self._last_heartbeat

    def queue_depth(self) -> int:
        """Get queue depth (for compatibility)."""
        return 0

    def get_status(self) -> dict:
        """Get client status information."""
        return {
            "running": self.is_running,
            "started": self._started,
            "mode": "http",
            "base_url": self._base_url,
            "event_forwarding": self._event_callback is not None,
        }

    def set_event_callback(self, callback: EventCallback | None) -> None:
        """Set the event callback for SSE forwarding.

        Args:
            callback: Callback function to forward events.
                     Signature: (event_type: str, data: dict) -> None
        """
        self._event_callback = callback


def _decode_error_body(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except Exception:
        return body.decode("utf-8", "replace")
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])
    return body.decode("utf-8", "replace")


def get_ipc_client(
    config: ChatServerConfig, event_callback: EventCallback | None = None
) -> IPCProgenitorClient:
    """Create an HTTP-based Progenitor client.

    Args:
        config: Backend configuration.
        event_callback: Optional callback for forwarding events to SSE.

    Returns:
        An IPCProgenitorClient instance.
    """
    return IPCProgenitorClient(config, event_callback=event_callback)


async def start_ipc_client_with_retry(
    client: IPCProgenitorClient,
    *,
    retry_delay: float = 5.0,
) -> None:
    """Start a Progenitor client in the background and keep retrying until it connects."""
    attempts = 0

    while True:
        try:
            await client.start()
            if attempts:
                logger.info("Progenitor client connected after %d retries", attempts)
            else:
                logger.info("Progenitor client connected")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            attempts += 1
            log = logger.warning if attempts == 1 or attempts % 12 == 0 else logger.debug
            log("Progenitor client unavailable, retrying in %.1fs: %s", retry_delay, e)
            await asyncio.sleep(retry_delay)
