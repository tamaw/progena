"""
Backend Main - FastAPI application with OpenAI-compatible interface.

This provides a standardized chat API that proxies to the Progenitor agent.

Communication: SSE (HTTP streaming) for reliability and real-time events.

Features (from text-generation-webui patterns):
- Streaming callbacks for real-time token streaming
- Conversation metadata with reasoning traces
- File attachments for document-grounded chat
- Session management for state persistence
- Graceful shutdown with request draining
"""

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, format_sse_event
from starlette.middleware.gzip import GZipMiddleware

from .agent_store import configure_agent_store
from .alerts import AlertInfo
from .attachment_blob_store import configure_attachment_blob_store, get_attachment_blob_store
from .attachment_routes import get_attachment_manager
from .attachment_routes import router as attachments_router
from .attachments import ATTACHMENT_CONTEXT_MARKER, format_attachments_for_context
from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .client import ProgenitorClient
from .config import ChatServerConfig
from .document_store import configure_default_document_store
from .knowledge_routes import router as knowledge_router
from .marketplace_routes import router as marketplaces_router
from .models import (
    ErrorDetail,
    ErrorResponse,
    ResponseRequest,
    StreamingResponseRequest,
)
from .plugin_routes import router as plugins_router
from .preferences_routes import router as preferences_router
from .repo_routes import router as repos_router
from .request_utils import request_correlation_id as _strict_request_correlation_id
from .session import (
    SessionConfig,
    SessionManager,
    SessionState,
    get_session_from_request,
    set_session_cookie,
)
from .shutdown import (
    GracefulShutdownManager,
    stop_client_safely,
)
from .sse_client import SSEProgenitorClient, get_sse_client, start_sse_client_with_retry

# New modules for enhanced features
from .telegram_client import (
    TelegramClient,
    TelegramDraftStreamingError,
    get_telegram_client,
    init_telegram_client,
    stop_telegram_client,
)
from .ui_routes import (
    broadcast_ui_event,
    close_all_sse_connections,
    has_active_ui_stream_subscribers,
    start_state_status_watcher,
    stop_state_status_watcher,
)
from .ui_routes import router as ui_router

logger = logging.getLogger(__name__)

# Global config and client
config = ChatServerConfig.from_env()
# Client is SSE-based (used) or subprocess-based (legacy, unused)
client: ProgenitorClient | SSEProgenitorClient | None = None
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
    expires_at: float | None


_response_streams: dict[str, ResponseStreamState] = {}


def _active_request_id_for_thread(thread_id: str) -> str | None:
    """Return the request_id of an in-flight (non-terminal) generation for ``thread_id``, if any.

    Lets a viewer that mounts or switches onto a thread after its
    ``chat.response.started`` broadcast already went out (a fresh page load, or
    a second tab) discover the in-flight request and join its delta stream.
    """
    for state in _response_streams.values():
        if state.thread_id == thread_id and not state.terminal_reached:
            return state.request_id
    return None


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
        expires_at=None,
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
    state.expires_at = time.time() + RESPONSE_STREAM_TTL_SECONDS
    _notify_response_stream_subscribers(state, None)
    _expire_response_stream_later(state.request_id, state)


def _expire_response_stream_later(request_id: str, state: ResponseStreamState) -> None:
    """Remove a terminal response stream after its replay grace period."""
    if state.expires_at is None:
        return

    async def expire() -> None:
        assert state.expires_at is not None
        await asyncio.sleep(max(0.0, state.expires_at - time.time()))
        if _response_streams.get(request_id) is state and time.time() >= state.expires_at:
            _response_streams.pop(request_id, None)

    _spawn_background_task(expire())


# The event loop only holds weak references to tasks: an unreferenced
# fire-and-forget task can be garbage-collected mid-await and die silently.
# Observed in production: a dispatched generation vanished between its 202
# acknowledgement and the runtime POST, losing the user's message.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """``asyncio.create_task`` with a strong reference held until completion."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def get_progenitor_liveness() -> bool:
    """Report whether the runtime is reachable right now, independent of Postgres.

    Backed by the SSE heartbeat/ack (see SSEProgenitorClient.is_running), not a
    stored timestamp - this is a live check, not a staleness heuristic.
    """
    return bool(client and client.is_running)


# Enhanced feature managers
session_manager: SessionManager = SessionManager(
    SessionConfig(
        persist_dir=None,
    )
)
graceful_shutdown: GracefulShutdownManager = GracefulShutdownManager(
    drain_timeout=30.0,
    total_timeout=60.0,
)


# ============================================================================
# Rate Limiting (in-memory, per-IP)
# ============================================================================
class RateLimiter:
    """Simple in-memory rate limiter using sliding window.

    Memory bounded: limits maximum tracked clients and performs periodic cleanup.
    """

    MAX_TRACKED_CLIENTS: int = 10000  # Maximum clients to track (memory protection)

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 500,
        cleanup_interval: int = 300,
        stale_client_timeout_seconds: int = 3600,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.cleanup_interval = cleanup_interval
        self.stale_client_timeout_seconds = stale_client_timeout_seconds
        self._minute_requests: dict[str, list[float]] = defaultdict(list)
        self._hour_requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def _cleanup_stale_clients(self, force: bool = False) -> int:
        """Remove stale client entries to prevent memory leak.

        Args:
            force: If True, cleanup regardless of interval.

        Returns:
            Number of clients removed.
        """
        now = time.time()
        if not force and (now - self._last_cleanup < self.cleanup_interval):
            return 0

        self._last_cleanup = now
        stale_threshold = now - self.stale_client_timeout_seconds

        # Find and remove stale clients
        stale_clients = []
        for client_id, timestamps in self._hour_requests.items():
            if not timestamps or max(timestamps) < stale_threshold:
                stale_clients.append(client_id)

        for client_id in stale_clients:
            self._minute_requests.pop(client_id, None)
            self._hour_requests.pop(client_id, None)

        if stale_clients:
            logger.debug("Cleaned up %d stale rate limit clients", len(stale_clients))

        return len(stale_clients)

    def _evict_oldest_clients(self, count: int) -> int:
        """Evict oldest clients when at capacity.

        Args:
            count: Number of clients to evict.

        Returns:
            Number of clients evicted.
        """
        if not self._hour_requests:
            return 0

        # Find clients with oldest last activity
        client_ages = []
        for client_id, timestamps in self._hour_requests.items():
            last_activity = max(timestamps) if timestamps else 0
            client_ages.append((client_id, last_activity))

        # Sort by oldest activity first
        client_ages.sort(key=lambda x: x[1])

        # Evict oldest
        evicted = 0
        for client_id, _ in client_ages[:count]:
            self._minute_requests.pop(client_id, None)
            self._hour_requests.pop(client_id, None)
            evicted += 1

        if evicted:
            logger.warning("Evicted %d oldest rate limit clients due to capacity", evicted)

        return evicted

    def is_allowed(self, client_id: str) -> tuple[bool, str | None]:
        """Check if request is allowed. Returns (allowed, reason)."""
        now = time.time()
        minute_ago = now - 60
        hour_ago = now - self.stale_client_timeout_seconds

        # Periodic cleanup of stale clients
        self._cleanup_stale_clients()

        # Check if we need to evict due to capacity
        current_clients = len(self._hour_requests)
        if current_clients >= self.MAX_TRACKED_CLIENTS:
            # Force cleanup first
            self._cleanup_stale_clients(force=True)

            # If still at capacity, evict oldest
            if len(self._hour_requests) >= self.MAX_TRACKED_CLIENTS:
                self._evict_oldest_clients(self.MAX_TRACKED_CLIENTS // 10)  # Evict 10%

        # Clean old entries for this client
        self._minute_requests[client_id] = [
            t for t in self._minute_requests[client_id] if t > minute_ago
        ]
        self._hour_requests[client_id] = [t for t in self._hour_requests[client_id] if t > hour_ago]

        # Check limits
        if len(self._minute_requests[client_id]) >= self.requests_per_minute:
            return False, f"Rate limit exceeded: {self.requests_per_minute} requests/minute"
        if len(self._hour_requests[client_id]) >= self.requests_per_hour:
            return False, f"Rate limit exceeded: {self.requests_per_hour} requests/hour"

        return True, None

    def record_request(self, client_id: str) -> None:
        """Record a request for rate limiting."""
        now = time.time()
        self._minute_requests[client_id].append(now)
        self._hour_requests[client_id].append(now)

    def get_remaining(self, client_id: str) -> dict[str, int]:
        """Get remaining requests for headers."""
        now = time.time()
        minute_count = len([t for t in self._minute_requests[client_id] if t > now - 60])
        hour_count = len(
            [
                t
                for t in self._hour_requests[client_id]
                if t > now - self.stale_client_timeout_seconds
            ]
        )
        return {
            "minute": max(0, self.requests_per_minute - minute_count),
            "hour": max(0, self.requests_per_hour - hour_count),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics for health endpoint."""
        active_clients = len(self._hour_requests)
        return {
            "active_clients": active_clients,
            "max_tracked_clients": self.MAX_TRACKED_CLIENTS,
            "capacity_percent": round(active_clients / self.MAX_TRACKED_CLIENTS * 100, 1),
            "requests_per_minute_limit": self.requests_per_minute,
            "requests_per_hour_limit": self.requests_per_hour,
        }


rate_limiter = RateLimiter(
    requests_per_minute=config.rate_limit_per_minute,
    requests_per_hour=config.rate_limit_per_hour,
    cleanup_interval=config.rate_limit_cleanup_interval_seconds,
    stale_client_timeout_seconds=config.rate_limit_stale_client_timeout_seconds,
)


def get_client_id(request: Request) -> str:
    """Extract client identifier for rate limiting."""
    # Use X-Forwarded-For if behind proxy, otherwise use client host
    forwarded = request.headers.get("X-Forwarded-For")
    if isinstance(forwarded, str) and forwarded:
        return forwarded.split(",")[0].strip()
    return str(request.client.host) if request.client else "unknown"


# ============================================================================
# Graceful Shutdown Management (OPS-002)
# ============================================================================


class ShutdownManager:
    """Manages graceful shutdown with request draining."""

    def __init__(self, drain_timeout: float = 30.0):
        self.drain_timeout = drain_timeout
        self._active_requests: int = 0
        self._shutting_down: bool = False
        self._shutdown_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def active_requests(self) -> int:
        return self._active_requests

    async def start_request(self) -> bool:
        """Start tracking a request. Returns False if shutting down."""
        async with self._lock:
            if self._shutting_down:
                return False
            self._active_requests += 1
            return True

    async def end_request(self) -> None:
        """Mark a request as complete."""
        async with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            if self._shutting_down and self._active_requests == 0 and self._shutdown_event:
                self._shutdown_event.set()

    async def initiate_shutdown(self) -> None:
        """Initiate graceful shutdown with request draining."""
        async with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._shutdown_event = asyncio.Event()

            if self._active_requests == 0:
                self._shutdown_event.set()

        logger.info(
            "Graceful shutdown initiated, waiting for %d active requests (timeout: %.1fs)",
            self._active_requests,
            self.drain_timeout,
        )

        try:
            await asyncio.wait_for(
                self._shutdown_event.wait(),
                timeout=self.drain_timeout,
            )
            logger.info("All requests drained successfully")
        except TimeoutError:
            logger.warning(
                "Shutdown timeout reached with %d requests still active",
                self._active_requests,
            )

    def get_status(self) -> dict[str, Any]:
        """Get shutdown status for health endpoint."""
        return {
            "shutting_down": self._shutting_down,
            "active_requests": self._active_requests,
            "drain_timeout_seconds": self.drain_timeout,
        }


shutdown_manager = ShutdownManager(drain_timeout=30.0)


async def _send_telegram_connected_message(
    telegram: TelegramClient,
    progenitor: ProgenitorClient | SSEProgenitorClient,
) -> None:
    """Trigger an agent-generated greeting after Telegram is initialized.

    This routes through the same backend -> Progenitor SSE request/response
    path as a normal user message, but is auto-invoked on connection.
    """
    prompt = (
        "Hi! I just connected to you on Telegram. "
        "Please reply with a short, friendly greeting and invite me to send a message."
    )
    request_id = f"telegram-connect-{uuid.uuid4().hex[:8]}"

    try:
        # Ensure the client is started/connected (we normally lazy-start).
        if not progenitor.is_running:
            await progenitor.start()

        response_text = (
            await progenitor.send_message(prompt, request_id=request_id)
        ).response.strip()
        if not response_text:
            raise RuntimeError("Empty greeting response from agent")

        sent = await telegram.send_message(response_text)
        if not sent:
            logger.warning("Telegram greeting send returned False")
    except Exception as e:
        logger.warning("Failed to send agent-generated Telegram greeting: %s", e)

        # Fallback should signal failure (if the agent path failed, we're not fully connected).
        try:
            sent = await telegram.send_message(
                "⚠️ Progenitor failed to connect to the agent. "
                "I can't respond yet — please check the backend/agent logs and try again."
            )
            if not sent:
                logger.warning("Fallback Telegram failure message send returned False")
        except Exception as e2:
            logger.warning("Failed to send fallback Telegram failure message: %s", e2)


async def _send_telegram_message(telegram: TelegramClient, content: str, event_type: str) -> None:
    """Send a message to Telegram (fire-and-forget helper).

    Args:
        telegram: The Telegram client instance.
        content: Message content to send.
        event_type: Type of event for logging.
    """
    try:
        success = await telegram.send_agent_response(content)
        if success:
            logger.info("Sent %s to Telegram (%d chars)", event_type, len(content))
        else:
            logger.warning("Failed to send %s to Telegram", event_type)
    except Exception as e:
        logger.error("Error sending to Telegram: %s", e)


def _build_alert_summary_prompt(alert: AlertInfo) -> str:
    """Build the follow-up prompt that turns an alert into a normal user-facing message."""
    return "\n".join(
        [
            "A backend alert was just created for the user.",
            "Use the `say_to_user` tool exactly once.",
            "Do not call `notify_user`, `ask_user`, or any other tools.",
            "Write a short alert-style summary for the user on their current platform.",
            "Keep the message faithful to the alert details, but summarize it clearly and naturally.",
            "After the tool call, reply with exactly: sent",
            "```json",
            json.dumps(
                {
                    "id": alert.id,
                    "type": alert.type,
                    "title": alert.title,
                    "message": alert.message,
                    "urgent": alert.urgent,
                    "requires_response": alert.requires_response,
                },
                indent=2,
            ),
            "```",
        ]
    )


async def _handle_alert_request(
    progenitor: ProgenitorClient | SSEProgenitorClient | None,
    *,
    title: str,
    body: str,
    level: str,
    urgent: bool,
    requires_response: bool,
    create_alert_record: Callable[..., AlertInfo],
    broadcast_alert_record: Callable[[AlertInfo], None],
    broadcast_alert_state: Callable[[], None],
) -> None:
    """Store an alert and ask Progenitor to summarize it through the normal user-message path."""
    if not body:
        return

    alert = create_alert_record(
        type=level,
        title=title,
        message=body,
        urgent=urgent,
        requires_response=requires_response,
    )
    broadcast_alert_record(alert)
    broadcast_alert_state()

    if progenitor is None:
        logger.warning(
            "Dropping alert summary request for %s because backend client is unavailable", alert.id
        )
        return

    try:
        if not progenitor.is_running:
            await progenitor.start()

        response_text = (
            await progenitor.send_message(
                _build_alert_summary_prompt(alert),
                request_id=f"alert-summary-{alert.id}",
            )
        ).response.strip()
        if response_text and response_text.lower() != "sent":
            logger.debug(
                "Alert summary helper returned non-sentinel text for %s: %s",
                alert.id,
                response_text,
            )
    except Exception as e:
        logger.warning("Failed to enqueue alert summary for %s: %s", alert.id, e)


async def _handle_telegram_agent_message(
    telegram: TelegramClient,
    progenitor: ProgenitorClient | SSEProgenitorClient,
    chat_id: str,
    message: str,
    metadata: dict[str, Any],
) -> str | None:
    """Handle an inbound Telegram chat by streaming the reply back to Telegram."""
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            logger.info(
                "Processing Telegram message from %s (attempt %d): %s",
                metadata.get("username", chat_id),
                attempt + 1,
                message[:50] + "..." if len(message) > 50 else message,
            )

            if not progenitor.is_running:
                logger.info("SSE client not running, starting...")
                await progenitor.start()

            request_id = f"telegram-{chat_id}-{uuid.uuid4().hex[:8]}"
            streamed_chars = await telegram.stream_agent_response_to_chat(
                chat_id,
                _stream_text_chunks(
                    progenitor.send_message_stream_events(message, request_id=request_id)
                ),
                message_thread_id=metadata.get("message_thread_id"),
            )

            logger.info(
                "Streamed Telegram response for %s (%d chars)",
                metadata.get("username", chat_id),
                streamed_chars,
            )
            if streamed_chars == 0:
                return "⚠️ Empty response from agent."
            return None

        except TelegramDraftStreamingError as e:
            logger.error("Telegram draft streaming failed: %s", e)
            return (
                "⚠️ Telegram native draft streaming failed. "
                "Please check the backend logs and bot API support."
            )
        except TimeoutError:
            logger.error("Timeout waiting for agent response")
            return "⏱️ Sorry, the request timed out. Please try again."
        except (ConnectionError, OSError) as e:
            logger.warning("Connection error on attempt %d: %s", attempt + 1, e)
            if attempt < max_retries:
                with suppress(Exception):
                    await progenitor.stop()
                await asyncio.sleep(1)
                continue
            logger.error("Failed after %d attempts: %s", max_retries + 1, e)
            return "⚠️ Connection error. Please try again in a moment."
        except Exception as e:
            logger.error("Error processing Telegram message: %s", e)
            return None

    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful shutdown (OPS-002).

    Enhanced with:
    - Session manager lifecycle
    - Attachment manager cleanup
    - Graceful shutdown with callbacks
    """
    global client
    sse_event_forwarding_task: asyncio.Task | None = None

    # Startup
    logger.info("Starting backend with enhanced features...")
    document_store = configure_default_document_store(config.database_url)
    attachment_blob_store = configure_attachment_blob_store(config.database_url)

    async def close_document_store() -> None:
        document_store.close()

    async def close_attachment_blob_store() -> None:
        attachment_blob_store.close()

    # Backend-owned agent data lives in a separate database on the same server.
    try:
        agent_store = configure_agent_store(
            config.database_url,
            config.backend_database_url,
            config.backend_database_name,
        )
    except Exception:  # noqa: BLE001 - backend must still start if agent DB is unavailable
        logger.exception("Failed to configure backend agent store")
        agent_store = None

    async def close_agent_store() -> None:
        if agent_store is not None:
            agent_store.close()

    # Initialize session manager
    await session_manager.start()
    logger.info("Session manager started")

    # Install graceful shutdown signal handlers
    graceful_shutdown.install_signal_handlers()

    # Register drain callbacks - these run at the start of draining to help
    # close long-running connections that would otherwise block draining
    graceful_shutdown.register_drain_callback(
        "close_sse_connections",
        close_all_sse_connections,
        priority=10,  # Run early to unblock request draining
        timeout=5.0,
    )

    # Register shutdown callbacks - these run after draining completes
    graceful_shutdown.register_callback(
        "close_document_store",
        close_document_store,
        priority=45,
        timeout=5.0,
    )
    graceful_shutdown.register_callback(
        "close_attachment_blob_store",
        close_attachment_blob_store,
        priority=45,
        timeout=5.0,
    )
    graceful_shutdown.register_callback(
        "close_agent_store",
        close_agent_store,
        priority=45,
        timeout=5.0,
    )
    graceful_shutdown.register_callback(
        "stop_session_manager",
        session_manager.stop,
        priority=50,
        timeout=5.0,
    )

    # Initialize Telegram client if enabled
    telegram_client = await init_telegram_client(config)
    if telegram_client:
        logger.info(
            "Telegram integration enabled (chat_id=%s)",
            config.telegram_chat_id[:8] + "..."
            if len(config.telegram_chat_id) > 8
            else config.telegram_chat_id,
        )
        # Register Telegram shutdown callback
        graceful_shutdown.register_callback(
            "stop_telegram_client",
            stop_telegram_client,
            priority=70,
            timeout=5.0,
        )

    # Initialize the Progenitor runtime HTTP client
    logger.info("Using Progenitor runtime client (base_url=%s)", config.progenitor_runtime_url)
    # Import broadcast helpers for runtime events and alert state.
    from .ui_routes import (
        broadcast_alert,
        broadcast_alerts_state,
        broadcast_schedule_snapshot,
        broadcast_ui_event,
        create_alert,
    )

    # Runtime events that imply the schedule document changed (a task fired and
    # was removed/restamped, or a schedule was created/edited from a chat).
    schedule_change_events = {"scheduled_task_triggered", "schedule_updated"}

    # Create a combined event callback that handles both SSE and Telegram
    def combined_event_callback(event_type: str, data: dict) -> None:
        """Route runtime events to SSE and/or Telegram.

        - Always broadcasts to SSE for any connected web clients
        - If no web clients connected, also sends to Telegram
        - Only forwards certain event types to Telegram (agent_message, agent_question, scheduled_response)
        """
        if event_type == "alert_request":
            metadata = data.get("metadata") or {}
            title = str(metadata.get("title") or "Alert").strip() or "Alert"
            body = str(data.get("content") or "").strip()
            level = str(metadata.get("level") or "info").strip().lower() or "info"
            urgent = bool(metadata.get("urgent")) or level in {"warning", "error"}
            requires_response = bool(metadata.get("requires_response"))

            _spawn_background_task(
                _handle_alert_request(
                    client,
                    title=title,
                    body=body,
                    level=level,
                    urgent=urgent,
                    requires_response=requires_response,
                    create_alert_record=create_alert,
                    broadcast_alert_record=broadcast_alert,
                    broadcast_alert_state=broadcast_alerts_state,
                )
            )
            return

        # Always broadcast to SSE
        metadata = data.get("metadata") or {}
        thread_id = str(metadata.get("thread_id") or "").strip() or None
        broadcast_ui_event(event_type, data, scope="chat", session_id=thread_id)

        # Reflect runtime-driven schedule changes on the canonical UI stream so
        # the Schedules page updates live (e.g. a one-off firing and removing
        # itself, or a schedule created from a chat).
        if event_type in schedule_change_events:
            try:
                broadcast_schedule_snapshot()
            except Exception:  # pragma: no cover - best-effort live update
                logger.debug("Failed to broadcast schedule snapshot", exc_info=True)

        # Check if we should also send to Telegram
        telegram = get_telegram_client()
        if telegram and not has_active_ui_stream_subscribers():
            # Only send actual message content to Telegram
            # Skip status updates, heartbeats, etc.
            telegram_event_types = {
                "scheduled_response",  # Scheduled task responses
                "agent_message",  # Direct agent messages
                "agent_question",  # Questions that should reach the user directly
            }

            if event_type in telegram_event_types:
                content = data.get("content", "")
                if content:
                    # Fire and forget - don't block SSE broadcast
                    _spawn_background_task(_send_telegram_message(telegram, content, event_type))

    client = get_sse_client(config, event_callback=combined_event_callback)

    # Register client shutdown callback
    if client:
        sse_event_forwarding_task = asyncio.create_task(start_sse_client_with_retry(client))

        async def stop_sse_event_forwarding_task() -> None:
            """Stop the background HTTP+SSE event-forwarding connector."""
            nonlocal sse_event_forwarding_task
            if sse_event_forwarding_task and not sse_event_forwarding_task.done():
                sse_event_forwarding_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sse_event_forwarding_task
            sse_event_forwarding_task = None

        graceful_shutdown.register_callback(
            "stop_sse_event_forwarding_task",
            stop_sse_event_forwarding_task,
            priority=25,
            timeout=5.0,
        )

        async def shutdown_progenitor_client() -> None:
            """Wrapper to ensure callback returns None."""
            await stop_client_safely(client, timeout=10.0, name="Progenitor")

        graceful_shutdown.register_callback(
            "stop_progenitor_client",
            shutdown_progenitor_client,
            priority=30,
            timeout=15.0,
        )

    # Set up Telegram message handler and start polling if enabled
    if telegram_client and client:

        async def handle_telegram_message(chat_id: str, message: str, metadata: dict) -> str | None:
            """Handle incoming Telegram messages by forwarding to Progenitor.

            Args:
                chat_id: The Telegram chat ID.
                message: The user's message text.
                metadata: Additional message metadata.

            Returns:
                Response from the agent, or None if failed.
            """
            assert client is not None, "Client should be available in this context"
            return await _handle_telegram_agent_message(
                telegram_client,
                client,
                chat_id,
                message,
                metadata,
            )

        telegram_client.set_message_handler(handle_telegram_message)
        await telegram_client.start_polling()
        logger.info("Telegram polling started with message handler")

        # Send a one-time agent-generated greeting now that Telegram + SSE are wired.
        _spawn_background_task(_send_telegram_connected_message(telegram_client, client))

    # Don't auto-start Progenitor - let it start on first request
    # This allows the server to start even if Progenitor has issues
    logger.info("Backend ready on %s:%d", config.host, config.port)
    logger.info(
        "Rate limiting: %d/min, %d/hour (enabled=%s)",
        config.rate_limit_per_minute,
        config.rate_limit_per_hour,
        config.rate_limit_enabled,
    )
    logger.info("Enhanced features: streaming, metadata, attachments, sessions")
    await start_state_status_watcher(reconnect_delay=2.0)
    graceful_shutdown.register_callback(
        "stop_state_status_watcher",
        stop_state_status_watcher,
        priority=40,
        timeout=5.0,
    )

    yield

    # Graceful Shutdown (OPS-002) - now using enhanced manager
    logger.info("Initiating graceful shutdown...")

    # Use the enhanced graceful shutdown manager
    await graceful_shutdown.initiate_shutdown()

    client = None
    logger.info("Backend shutdown complete")


app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    lifespan=lifespan,
)
app.include_router(ui_router)
app.include_router(knowledge_router)
app.include_router(marketplaces_router)
app.include_router(repos_router)
app.include_router(plugins_router)
app.include_router(preferences_router)
app.include_router(attachments_router)

# Note: This backend is API-only. Frontend is served separately via npm/vite.
# The frontend proxies /api and /v1 requests to this backend.
logger.info("Backend running in API-only mode (no static file serving)")


# Explicit architecture exception: Docker and container supervisors need a
# dependency-free liveness probe. This route is not a browser API surface and
# intentionally returns no application data; all browser application data still
# travels through /api/ui/stream.
@app.get("/healthz", include_in_schema=False)
async def docker_healthz() -> Response:
    """Return 200 when the backend process is accepting HTTP requests."""
    return Response(status_code=200)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress finite JSON responses once they exceed a small threshold. Starlette
# deliberately leaves text/event-stream responses uncompressed so the canonical
# and request-scoped SSE lanes keep low-latency frame delivery.
app.add_middleware(
    GZipMiddleware,
    minimum_size=1024,
    compresslevel=6,
)


@app.middleware("http")
async def request_tracking_middleware(request: Request, call_next):
    """Track active requests for graceful shutdown (OPS-002)."""

    # Check if we're accepting new requests
    if not await shutdown_manager.start_request():
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=ErrorDetail(
                    message="Server is shutting down",
                    type="service_unavailable",
                )
            ).model_dump(),
            headers={"Retry-After": "30"},
        )

    try:
        return await call_next(request)
    finally:
        await shutdown_manager.end_request()


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Add request ID to all requests for tracing."""
    # Get or generate request ID
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"

    # Store in request state for access by handlers
    request.state.request_id = request_id

    # Log request with ID
    logger.info(
        "Request %s: %s %s",
        request_id,
        request.method,
        request.url.path,
    )

    # Update activity timestamp
    _update_activity()

    # Process request
    response = await call_next(request)

    # Add request ID to response headers
    response.headers["X-Request-ID"] = request_id

    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Apply rate limiting to API endpoints."""
    # Skip rate limiting for documentation endpoints and UI event-only routes.
    documentation_paths = ("/docs", "/openapi.json")
    if not config.rate_limit_enabled:
        return await call_next(request)
    if request.url.path in documentation_paths or request.url.path == "/healthz":
        return await call_next(request)
    if request.url.path.startswith("/api/ui/") and not (
        request.url.path == "/api/ui/responses" or request.url.path.startswith("/api/ui/responses/")
    ):
        return await call_next(request)
    if request.url.path.startswith("/ui"):
        return await call_next(request)

    client_id = get_client_id(request)
    allowed, reason = rate_limiter.is_allowed(client_id)

    if not allowed:
        logger.warning("Rate limit exceeded for client %s: %s", client_id, reason)
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                error=ErrorDetail(
                    message=reason or "Rate limit exceeded",
                    type="rate_limit_exceeded",
                )
            ).model_dump(),
            headers={"Retry-After": "60"},
        )

    # Record the request and continue
    rate_limiter.record_request(client_id)
    response = await call_next(request)

    # Add rate limit headers
    remaining = rate_limiter.get_remaining(client_id)
    response.headers["X-RateLimit-Limit-Minute"] = str(config.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining-Minute"] = str(remaining["minute"])
    response.headers["X-RateLimit-Limit-Hour"] = str(config.rate_limit_per_hour)
    response.headers["X-RateLimit-Remaining-Hour"] = str(remaining["hour"])

    return response


@app.middleware("http")
async def request_metrics_middleware(request: Request, call_next):
    """Expose finite-request timing and log transport-level measurements."""
    started_at = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started_at) * 1000
    content_type = response.headers.get("content-type", "")
    is_event_stream = content_type.startswith("text/event-stream")

    # SSE connections are intentionally excluded: their duration is the
    # lifetime of the connection, not the cost of producing one response.
    if not is_event_stream:
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"

    logger.info(
        "API request completed: %s %s status=%s duration_ms=%.2f content_length=%s request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        response.headers.get("content-length"),
        getattr(request.state, "request_id", None),
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for API errors."""
    logger.error("API error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(
                message=str(exc),
                type="internal_error",
            )
        ).model_dump(),
    )


_last_activity_time = time.time()


def _update_activity():
    """Update last activity timestamp."""
    global _last_activity_time
    _last_activity_time = time.time()


def _request_correlation_id(request: Request, header_request_id: str | None) -> str:
    """Resolve middleware correlation, with an explicit standalone fallback."""
    try:
        return _strict_request_correlation_id(request, header_request_id)
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
                "chat.response.resync_required",
                {"request_id": state.request_id, "reason": "invalid_cursor"},
            )
            return

        retained_frames = list(state.frames)
        if cursor_status == "ok":
            assert cursor_seq is not None
            if retained_frames and cursor_seq < retained_frames[0].seq - 1:
                yield encode_event(
                    "chat.response.resync_required",
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
                    "chat.response.heartbeat",
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


@app.get("/api/ui/responses/{request_id}/stream")
async def response_stream(
    request_id: str,
    after: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Serve replayable response content without loading the global UI stream."""
    if not request_id.strip():
        raise HTTPException(status_code=404, detail="Unknown response stream")

    state = _response_streams.get(request_id)
    now = time.time()
    if state is None or (state.expires_at is not None and now >= state.expires_at):
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


@app.post("/api/ui/responses", status_code=202, response_model=UiRequestAcknowledgement)
async def responses(
    request: Request,
    response: Response,
    body: ResponseRequest,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Dispatch a browser generation and acknowledge its lifecycle on ui.event.

    Streaming response content travels on the request-scoped response SSE endpoint.
    """
    request_id = _request_correlation_id(request, x_request_id)

    try:
        if body.stream:
            StreamingResponseRequest.validate_for_streaming(body)
        last_user_msg = _extract_response_input(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    session = await get_session_from_request(request, session_manager)
    thread_id, request_metadata = _build_chat_request_metadata(session, body.metadata)
    last_user_msg = _resolve_message_attachments(last_user_msg, request_metadata, thread_id)
    await session_manager.record_chat_activity(
        session.session_id,
        message_increment=1,
    )

    response_id = f"resp-{uuid.uuid4().hex[:8]}"
    _active_response_streams[request_id] = asyncio.Event()
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
    broadcast_ui_event(
        "chat.response.started",
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
    return acknowledgement_response(request_id, ["chat.response.started"])


@app.put(
    "/api/ui/responses/{generation_id}/cancel",
    status_code=202,
    response_model=UiRequestAcknowledgement,
)
async def cancel_response(
    generation_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
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

    progenitor_cancelled = False
    if client is not None:
        cancel_request = getattr(client, "cancel_request", None)
        if callable(cancel_request):
            result = cancel_request(target_request_id)
            progenitor_cancelled = (
                bool(await result) if inspect.isawaitable(result) else bool(result)
            )

    if cancel_event is None and not progenitor_cancelled:
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
    broadcast_ui_event(
        "chat.response.cancel_requested",
        cancellation_payload,
        scope="chat",
        request_id=request_id,
    )
    if cancel_event is None:
        # A generation accepted by another backend instance has no local runner
        # to emit the target terminal event after a successful remote cancel.
        broadcast_ui_event(
            "chat.response.cancelled",
            cancellation_payload,
            scope="chat",
            request_id=target_request_id,
        )

    return acknowledgement_response(request_id, ["chat.response.cancel_requested"])


def _extract_response_input(request: ResponseRequest) -> str:
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

    manager = get_attachment_manager()
    blob_store = get_attachment_blob_store()
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
    context_block = format_attachments_for_context(resolved)
    if not context_block:
        return message
    if not message.strip():
        return f"{ATTACHMENT_CONTEXT_MARKER}\n{context_block}"
    return f"{message}\n\n{ATTACHMENT_CONTEXT_MARKER}\n{context_block}"


def _default_conversation_id(session: SessionState) -> str:
    """Return the stable default LangGraph thread for a web session."""
    return f"thread_{session.session_id}"


def _build_chat_request_metadata(
    session: SessionState,
    request_metadata: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resolve request metadata onto a stable, session-backed thread id."""
    metadata = dict(request_metadata or {})
    requested_thread_id = str(
        metadata.get("thread_id") or metadata.get("conversation_id") or ""
    ).strip()
    thread_id = requested_thread_id or _default_conversation_id(session)
    metadata["thread_id"] = thread_id
    return thread_id, metadata


def _attach_session_cookie(
    request: Request,
    session: SessionState,
    response: Response,
) -> Response:
    """Set the session cookie when the request has no matching cookie yet."""
    if request.cookies.get("progenitor_session") != session.session_id:
        set_session_cookie(response, session)
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
    assert client is not None, "Client must be initialized"
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
        broadcast_ui_event(
            "chat.response.cancelled",
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
            async for item in client.send_message_stream_events(
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
                            "chat.response.delta",
                            response_payload(
                                delta=chunk,
                                request_id=request_id,
                                **runtime_stream_timing(item),
                            ),
                        )
                elif item_type == "stream_event":
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
                            "chat.response.event",
                            payload,
                        )
            if cancel_event.is_set():
                logger.info("Response stream cancelled for %s", request_id)
                emit_cancelled()
                return
        else:
            response_details = await client.send_message(
                message,
                request_id=request_id,
                metadata=metadata,
                include_reasoning=include_reasoning,
            )
            if cancel_event.is_set():
                emit_cancelled()
                return

            reasoning_text = (response_details.reasoning or "").strip()
            broadcast_ui_event(
                "chat.response.delta",
                response_payload(
                    delta=response_details.response,
                    reasoning=reasoning_text or None,
                    reasoning_tokens=response_details.reasoning_tokens,
                ),
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
            )

        broadcast_ui_event(
            "chat.response.completed",
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
            broadcast_ui_event(
                "chat.response.failed",
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
            broadcast_ui_event(
                "chat.response.failed",
                response_payload(error=str(error)),
                scope="chat",
                session_id=thread_id,
                request_id=request_id,
            )
            mark_response_stream_terminal()
    finally:
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
