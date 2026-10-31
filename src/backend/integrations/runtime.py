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
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import quote

import httpx

import src.backend.events.contracts as events_contracts
import src.backend.lifecycle.state as lifecycle_state
from src.backend.chat.models import AgentResponse, normalize_token_usage
from src.backend.config import ChatServerConfig
from src.progena.routines.lifecycle import ROUTINE_RUN_CONTROL_STATUSES

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


class RuntimeQueueError(RuntimeError):
    """A queue request the runtime rejected or could not process."""

    def __init__(
        self, status_code: int, detail: str, *, snapshot: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.snapshot = snapshot


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
        metadata = event.get("metadata")
        if event_type.startswith("chat_queue_execution_"):
            self._forward_queue_execution_event(event_type, metadata)
            return
        if event_type == "chat_queue_updated":
            if not isinstance(metadata, dict):
                logger.warning("Ignoring chat queue event with invalid metadata")
                return
            snapshot = {
                key: metadata.get(key) for key in ("conversation_id", "revision", "messages")
            }
            if (
                not isinstance(snapshot["conversation_id"], str)
                or not snapshot["conversation_id"]
                or not isinstance(snapshot["revision"], int)
                or not isinstance(snapshot["messages"], list)
            ):
                logger.warning("Ignoring chat queue event with invalid snapshot")
                return
            try:
                self._event_callback(events_contracts.UiEvent.CHAT_QUEUE_UPDATED, snapshot)
            except Exception as exc:
                logger.warning("Error in event callback: %s", exc)
            return

        content = event.get("content", "")
        logger.info(
            "Forwarding event to SSE: type=%s, content=%s",
            event_type,
            str(content)[:100] if content else "",
        )

        payload: dict[str, Any] = {
            "content": content,
            "metadata": metadata if isinstance(metadata, dict) else {},
            "timestamp": event.get("timestamp"),
        }
        images = event.get("images")
        if isinstance(images, list) and images:
            payload["images"] = images

        try:
            self._event_callback(event_type, payload)
        except Exception as exc:
            logger.warning("Error in event callback: %s", exc)

    def _forward_queue_execution_event(self, event_type: str, metadata: object) -> None:
        """Translate a deferred runtime turn into normal browser lifecycle events."""
        if not isinstance(metadata, dict):
            logger.warning("Ignoring queue execution event with invalid metadata")
            return
        conversation_id = metadata.get("conversation_id")
        request_id = metadata.get("request_id")
        message_id = metadata.get("message_id")
        response = metadata.get("response")
        if (
            not isinstance(conversation_id, str)
            or not conversation_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(message_id, str)
            or not message_id
        ):
            logger.warning("Ignoring queue execution event with invalid metadata")
            return

        payload: dict[str, Any] = {
            "id": request_id,
            "thread_id": conversation_id,
            "message_id": message_id,
            "request_id": request_id,
            "queue_execution": True,
        }
        content_request_id = metadata.get("content_request_id")
        if isinstance(content_request_id, str) and content_request_id:
            payload["content_request_id"] = content_request_id
        if event_type == "chat_queue_execution_started":
            canonical_event = events_contracts.UiEvent.CHAT_RESPONSE_STARTED
        elif event_type == "chat_queue_execution_completed":
            if not isinstance(response, dict):
                logger.warning("Ignoring queue completion without response")
                return
            canonical_event = events_contracts.UiEvent.CHAT_RESPONSE_COMPLETED
            payload["response"] = response
            payload["completed"] = True
        elif event_type == "chat_queue_execution_failed":
            if not isinstance(response, dict):
                logger.warning("Ignoring queue failure without response")
                return
            canonical_event = events_contracts.UiEvent.CHAT_RESPONSE_FAILED
            payload["response"] = response
            error = response.get("error")
            payload["error"] = str(error) if error else "Queued response failed"
        else:
            logger.warning("Ignoring unknown queue execution event: %s", event_type)
            return
        callback = self._event_callback
        if callback is None:
            return
        try:
            callback(canonical_event, payload)
        except Exception as exc:
            logger.warning("Error in event callback: %s", exc)

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
                    response_payload = data.get("response")
                    if (
                        isinstance(response_payload, dict)
                        and response_payload.get("status") == "queued"
                    ):
                        yield {"type": "queued", "request_id": request_id}
                        return
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
        if payload.get("status") not in ROUTINE_RUN_CONTROL_STATUSES:
            raise RuntimeControlError(502, "Runtime returned an invalid routine control status")
        if not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
            raise RuntimeControlError(502, "Runtime control response is missing session_id")
        if not isinstance(payload.get("timestamp"), str) or not payload["timestamp"]:
            raise RuntimeControlError(502, "Runtime control response is missing timestamp")
        return payload

    async def get_chat_queue(self, conversation_id: str) -> dict[str, Any]:
        """Return the runtime-owned durable queue for one conversation."""
        return await self._request_chat_queue("GET", conversation_id)

    async def apply_chat_queue_action(
        self, conversation_id: str, action: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply one idempotent queue action and return its resulting snapshot."""
        return await self._request_chat_queue("POST", conversation_id, action=action)

    async def _request_chat_queue(
        self, method: str, conversation_id: str, *, action: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not conversation_id.strip():
            raise RuntimeQueueError(400, "Conversation ID is required")
        try:
            if not self._started:
                await self.start()
            assert self._client is not None
            path = f"/v1/conversations/{quote(conversation_id, safe='')}/queue"
            if method == "POST":
                path = f"{path}/actions"
            response = await self._client.request(
                method,
                path,
                json=action,
                timeout=httpx.Timeout(CANCEL_TIMEOUT_SECONDS),
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeQueueError(503, f"Runtime queue request failed: {exc}") from exc

        payload = _response_json_object(response)
        if response.status_code != 200:
            snapshot = (
                payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None
            )
            detail = _runtime_error_detail(payload, response.content)
            status_code = response.status_code if 400 <= response.status_code < 500 else 502
            raise RuntimeQueueError(status_code, detail, snapshot=snapshot)
        if payload.get("conversation_id") != conversation_id:
            raise RuntimeQueueError(502, "Runtime returned a queue for a different conversation")
        if not isinstance(payload.get("revision"), int) or not isinstance(
            payload.get("messages"), list
        ):
            raise RuntimeQueueError(502, "Runtime returned an invalid queue snapshot")
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


def _response_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _runtime_error_detail(payload: dict[str, Any], body: bytes) -> str:
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return _decode_error_body(body)


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
