"""
HTTP-based Progena Client for Backend.

Talks to the Progena runtime's persistent HTTP + SSE endpoints
(``POST /v1/responses``, ``POST /v1/responses/{response_id}/cancel``,
``DELETE /v1/conversations/{id}``, ``GET /v1/events``).

Features:
- HTTP/SSE communication (no custom framing)
- Native streaming support
- Automatic event-stream reconnection
- Event forwarding from agent to frontend via SSE
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast
from urllib.parse import quote

import httpx

import src.backend.lifecycle.state as lifecycle_state
from src.backend.chat.models import AgentResponse, normalize_token_usage
from src.backend.config import ChatServerConfig

logger = logging.getLogger(__name__)

# Callback used to forward runtime events to the backend's UI stream.
EventCallback = Callable[[str, dict], None]

HEALTH_PROBE_ATTEMPTS = 10
HEALTH_PROBE_RETRY_DELAY_SECONDS = 1.0
HEALTH_POLL_INTERVAL_SECONDS = 10.0
HEARTBEAT_STALE_SECONDS = 30.0
EVENTS_RECONNECT_DELAY_SECONDS = 5.0
CANCEL_TIMEOUT_SECONDS = 5.0
TASK_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class RuntimeControlError(RuntimeError):
    """A routine-control request the runtime rejected or could not process."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


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


class SSEProgenaClient:
    """Client that communicates with the Progena runtime via HTTP + SSE.

    Talks to the runtime's persistent HTTP server for chat requests,
    cancellation, and background event forwarding to the frontend via SSE.
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
        return self.config.progena_runtime_url

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
                    f"Failed to reach Progena runtime HTTP server at {self._base_url}"
                )

            self._client = client
            self._last_heartbeat = time.time()
            self._health_task = asyncio.create_task(self._health_poll_loop())
            self._events_task = asyncio.create_task(self._events_loop())

            self._started = True
            logger.info("HTTP Progena client started (%s)", self._base_url)

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

    async def get_thread_state(self, thread_id: str) -> dict[str, Any] | None:
        """Read one runtime-owned checkpoint status without inferring on failure."""
        client = self._client
        if client is None:
            return None
        try:
            response = await client.get(
                f"/v1/threads/{quote(thread_id, safe=':')}/state",
                timeout=httpx.Timeout(1.5, connect=0.5),
            )
            if response.status_code != 200:
                return None
            payload = response.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

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
        usage: dict[str, int] | None = None
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
                    parsed_usage = normalize_token_usage(event_metadata.get("usage"))
                    if parsed_usage is not None:
                        usage = parsed_usage
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
            usage=usage,
        )

    async def send_message_stream_events(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a message and stream structured chat events.

        Posts to the runtime's ``/v1/responses`` endpoint (OpenAI Responses
        API shape) and translates the returned SSE frames back into the
        existing internal dicts so the rest of the backend stays unchanged.
        """
        if not self._started:
            await self.start()
        assert self._client is not None

        request_metadata = dict(metadata or {})
        if request_id:
            # The runtime uses metadata.request_id as the response id, which is
            # what POST /v1/responses/{id}/cancel addresses.
            request_metadata["request_id"] = request_id
        body: dict[str, Any] = {"input": message, "stream": True}
        if request_metadata:
            body["metadata"] = request_metadata
        thread_id = request_metadata.get("thread_id")
        if thread_id:
            body["conversation"] = thread_id

        request_timeout = httpx.Timeout(5.0, read=self.config.progena_timeout)
        async with self._client.stream(
            "POST", "/v1/responses", json=body, timeout=request_timeout
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(f"Progena error: {_decode_error_body(error_body)}")

            async for _event_name, data in _iter_sse_frames(response):
                event_type = data.get("type", "")

                if event_type == "response.output_text.delta":
                    yield {
                        "type": "stream_chunk",
                        "content": data.get("delta", ""),
                        "is_final": False,
                        "request_id": request_id,
                        "runtime_emitted_at_ms": data.get("runtime_emitted_at_ms"),
                        "runtime_stream_seq": data.get("runtime_stream_seq"),
                    }
                elif event_type == "progena.stream_event":
                    yielded: dict[str, Any] = {
                        "type": "stream_event",
                        "event_type": data.get("event_type", ""),
                        "content": data.get("content", ""),
                        "metadata": data.get("metadata", {}),
                        "request_id": request_id,
                        "runtime_emitted_at_ms": data.get("runtime_emitted_at_ms"),
                        "runtime_stream_seq": data.get("runtime_stream_seq"),
                    }
                    images = data.get("images")
                    if isinstance(images, list) and images:
                        yielded["images"] = images
                    yield yielded
                elif event_type == "response.completed":
                    yield {
                        "type": "stream_chunk",
                        "content": "",
                        "is_final": True,
                        "request_id": request_id,
                    }
                    return
                elif event_type == "response.incomplete":
                    return
                elif event_type == "response.failed":
                    error = data.get("response", {}).get("error", {})
                    raise RuntimeError(f"Progena error: {error.get('message', 'unknown error')}")
                # All other events (response.created, response.in_progress, …) are ignored.

    async def cancel_request(self, request_id: str) -> bool:
        """Best-effort cancellation of an active response."""
        if not request_id.strip():
            return False

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(CANCEL_TIMEOUT_SECONDS)
            ) as client:
                response = await client.post(f"/v1/responses/{quote(request_id, safe='')}/cancel")
                response.raise_for_status()
                return bool(response.json().get("status") == "cancelled")
        except Exception as e:
            logger.warning("Failed to send HTTP cancel for %s: %s", request_id, e)
            return False

    async def control_routine_run(
        self,
        agent_id: str,
        *,
        thread_id: str,
        request_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Forward one exact routine-run control request to the runtime.

        ``request_id`` is the target execution attempt, not this HTTP
        operation's correlation id.  The runtime owns active-task identity and
        returns only an observed lifecycle state; a pending acknowledgement
        never claims the underlying graph has already stopped.
        """
        if not agent_id or not thread_id or not request_id or action not in {"stop", "cancel"}:
            raise RuntimeControlError(400, "Invalid routine run control request")
        try:
            if not self._started:
                await self.start()
            assert self._client is not None
            response = await self._client.post(
                "/v1/routine-runs/control",
                json={
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "request_id": request_id,
                    "action": action,
                },
                timeout=httpx.Timeout(CANCEL_TIMEOUT_SECONDS),
            )
        except RuntimeControlError:
            raise
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeControlError(503, f"Runtime control request failed: {exc}") from exc

        if response.status_code != 200:
            status_code = response.status_code if 400 <= response.status_code < 500 else 502
            raise RuntimeControlError(status_code, _decode_error_body(response.content))
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeControlError(502, "Runtime returned an invalid control response") from exc
        if not isinstance(payload, dict):
            raise RuntimeControlError(502, "Runtime returned an invalid control response")
        if any(
            payload.get(key) != expected
            for key, expected in (
                ("agent_id", agent_id),
                ("thread_id", thread_id),
                ("request_id", request_id),
            )
        ):
            raise RuntimeControlError(502, "Runtime returned control for a different routine run")
        if payload.get("status") not in {"stopping", "cancelling", "stopped", "cancelled"}:
            raise RuntimeControlError(502, "Runtime returned an invalid routine control status")
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
            raise RuntimeControlError(502, "Runtime control response is missing session_id")
        if not isinstance(payload.get("timestamp"), str) or not payload["timestamp"]:
            raise RuntimeControlError(502, "Runtime control response is missing timestamp")
        return payload

    async def delete_conversation(self, thread_id: str) -> bool:
        """Best-effort deletion of a conversation thread on the runtime."""
        if not thread_id.strip():
            return False

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url, timeout=httpx.Timeout(10.0)
            ) as client:
                response = await client.delete(f"/v1/conversations/{quote(thread_id, safe='')}")
                return response.status_code == 200
        except Exception as e:
            logger.warning("Failed to delete conversation %s: %s", thread_id, e)
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
    if isinstance(payload, dict):
        if "error" in payload:
            return str(payload["error"])
        if "detail" in payload:
            return str(payload["detail"])
    return body.decode("utf-8", "replace")


def get_sse_client(
    config: ChatServerConfig, event_callback: EventCallback | None = None
) -> SSEProgenaClient:
    """Create an HTTP-based Progena client.

    Args:
        config: Backend configuration.
        event_callback: Optional callback for forwarding events to SSE.

    Returns:
        An SSEProgenaClient instance.
    """
    return SSEProgenaClient(config, event_callback=event_callback)


async def start_sse_client_with_retry(
    client: SSEProgenaClient,
    *,
    retry_delay: float = 5.0,
) -> None:
    """Start a Progena client in the background and keep retrying until it connects."""
    attempts = 0

    while True:
        try:
            await client.start()
            if attempts:
                logger.info("Progena client connected after %d retries", attempts)
            else:
                logger.info("Progena client connected")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            attempts += 1
            log = logger.warning if attempts == 1 or attempts % 12 == 0 else logger.debug
            log("Progena client unavailable, retrying in %.1fs: %s", retry_delay, e)
            await asyncio.sleep(retry_delay)


def _progena_runtime_live() -> bool:

    return bool(get_progena_liveness())


def get_progena_liveness() -> bool:
    """Report whether the runtime is reachable right now, independent of Postgres.

    Backed by the SSE heartbeat/ack (see SSEProgenaClient.is_running), not a
    stored timestamp - this is a live check, not a staleness heuristic.
    """
    return bool(lifecycle_state.client and lifecycle_state.client.is_running)


async def get_thread_state(thread_id: str) -> dict[str, Any] | None:
    """Read runtime checkpoint state through the shared runtime HTTP client."""
    client = lifecycle_state.client
    reader = getattr(client, "get_thread_state", None)
    if not callable(reader):
        return None
    return await cast(Callable[[str], Awaitable[dict[str, Any] | None]], reader)(thread_id)
