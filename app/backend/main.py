"""
Backend Main - FastAPI application with OpenAI-compatible interface.

This provides a standardized chat API that proxies to the Progenitor agent.

Communication: IPC (socket-based) for reliability and streaming.

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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .attachments import (
    AttachmentConfig,
    AttachmentManager,
    create_attachment_from_upload,
)
from .client import ProgenitorClient
from .config import ChatServerConfig
from .conversation import (
    ConversationHistory,
)
from .ipc_client import IPCProgenitorClient, get_ipc_client
from .models import (
    ErrorDetail,
    ErrorResponse,
    ModelInfo,
    ModelsResponse,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponsePayload,
    ResponseRequest,
    SimpleChatRequest,
    StreamingResponseRequest,
    Usage,
)
from .session import (
    SessionConfig,
    SessionManager,
    SettingsManager,
    get_session_from_request,
    set_session_cookie,
)
from .shutdown import (
    GracefulShutdownManager,
    stop_client_safely,
)

# New modules for enhanced features
from .telegram_client import (
    TelegramClient,
    get_telegram_client,
    init_telegram_client,
    stop_telegram_client,
)
from .ui_routes import close_all_sse_connections, has_active_chat_subscribers
from .ui_routes import router as ui_router

logger = logging.getLogger(__name__)

# Global config and client
config = ChatServerConfig.from_env()
# Client can be either IPC-based or subprocess-based
client: ProgenitorClient | IPCProgenitorClient | None = None

# Enhanced feature managers
session_manager: SessionManager = SessionManager(SessionConfig(
    persist_dir=Path("data/sessions") if config.host else None,
))
attachment_manager: AttachmentManager = AttachmentManager(AttachmentConfig(
    cache_dir=Path("data/attachments"),
))
conversation_history: ConversationHistory = ConversationHistory()
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
        self._hour_requests[client_id] = [
            t for t in self._hour_requests[client_id] if t > hour_ago
        ]

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
        hour_count = len([t for t in self._hour_requests[client_id] if t > now - self.stale_client_timeout_seconds])
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
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    progenitor: ProgenitorClient | IPCProgenitorClient,
) -> None:
    """Trigger an agent-generated greeting after Telegram is initialized.

    This routes through the same backend -> Progenitor IPC request/response
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

        response_text = (await progenitor.send_message(prompt, request_id=request_id)).strip()
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


async def _send_telegram_message(
    telegram: TelegramClient,
    content: str,
    event_type: str
) -> None:
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful shutdown (OPS-002).

    Enhanced with:
    - Session manager lifecycle
    - Attachment manager cleanup
    - Graceful shutdown with callbacks
    """
    global client

    # Startup
    logger.info("Starting backend with enhanced features...")

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
        "stop_session_manager",
        session_manager.stop,
        priority=50,
        timeout=5.0,
    )

    async def _clear_attachment_cache():
        """Async wrapper for sync clear_cache."""
        attachment_manager.clear_cache()

    graceful_shutdown.register_callback(
        "clear_attachment_cache",
        _clear_attachment_cache,
        priority=60,
        timeout=2.0,
    )

    # Initialize Telegram client if enabled
    telegram_client = await init_telegram_client(config)
    if telegram_client:
        logger.info(
            "Telegram integration enabled (chat_id=%s)",
            config.telegram_chat_id[:8] + "..." if len(config.telegram_chat_id) > 8 else config.telegram_chat_id
        )
        # Register Telegram shutdown callback
        graceful_shutdown.register_callback(
            "stop_telegram_client",
            stop_telegram_client,
            priority=70,
            timeout=5.0,
        )

    # Initialize IPC client
    logger.info(
        "Using IPC client (host=%s, port=%d)",
        config.progenitor_ipc_host,
        config.progenitor_ipc_port,
    )
    # Import broadcast_chat_event for IPC events (chat, alerts, tasks)
    from .ui_routes import broadcast_chat_event

    # Create a combined event callback that handles both SSE and Telegram
    def combined_event_callback(event_type: str, data: dict) -> None:
        """Route IPC events to SSE and/or Telegram.

        - Always broadcasts to SSE for any connected web clients
        - If no web clients connected, also sends to Telegram (if enabled)
        - Only forwards certain event types to Telegram (agent_message, scheduled_response)
        """
        # Always broadcast to SSE
        broadcast_chat_event(event_type, data)

        # Check if we should also send to Telegram
        telegram = get_telegram_client()
        if telegram and not has_active_chat_subscribers():
            # Only send actual message content to Telegram
            # Skip status updates, heartbeats, etc.
            telegram_event_types = {
                "scheduled_response",  # Scheduled task responses
                "agent_message",       # Direct agent messages
            }

            if event_type in telegram_event_types:
                content = data.get("content", "")
                if content:
                    # Fire and forget - don't block SSE broadcast
                    asyncio.create_task(
                        _send_telegram_message(telegram, content, event_type)
                    )

    client = get_ipc_client(config, event_callback=combined_event_callback)

    # Register client shutdown callback
    if client:
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
        async def handle_telegram_message(
            chat_id: str,
            message: str,
            metadata: dict
        ) -> str | None:
            """Handle incoming Telegram messages by forwarding to Progenitor.

            Args:
                chat_id: The Telegram chat ID.
                message: The user's message text.
                metadata: Additional message metadata.

            Returns:
                Response from the agent, or None if failed.
            """
            max_retries = 2
            for attempt in range(max_retries + 1):
                try:
                    logger.info(
                        "Processing Telegram message from %s (attempt %d): %s",
                        metadata.get("username", chat_id),
                        attempt + 1,
                        message[:50] + "..." if len(message) > 50 else message
                    )

                    # Ensure client is available
                    assert client is not None, "Client should be available in this context"

                    # Ensure client is started/connected
                    if not client.is_running:
                        logger.info("IPC client not running, starting...")
                        await client.start()

                    # Create a unique request ID for correlation
                    request_id = f"telegram-{chat_id}-{uuid.uuid4().hex[:8]}"

                    # Use streaming API and collect chunks into full response
                    # (The Progenitor loop always streams, so we must use stream mode)
                    response_chunks = []
                    async for chunk in client.send_message_stream(message, request_id=request_id):
                        if chunk:
                            response_chunks.append(chunk)

                    response = "".join(response_chunks)

                    logger.info(
                        "Got response for Telegram message (%d chars)",
                        len(response) if response else 0
                    )

                    return response if response else None

                except TimeoutError:
                    logger.error("Timeout waiting for agent response")
                    return "⏱️ Sorry, the request timed out. Please try again."
                except (ConnectionError, OSError) as e:
                    logger.warning("Connection error on attempt %d: %s", attempt + 1, e)
                    if attempt < max_retries:
                        # Try to reconnect
                        try:
                            assert client is not None
                            await client.stop()
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                        continue
                    logger.error("Failed after %d attempts: %s", max_retries + 1, e)
                    return "⚠️ Connection error. Please try again in a moment."
                except Exception as e:
                    logger.error("Error processing Telegram message: %s", e)
                    return None

            return None

        telegram_client.set_message_handler(handle_telegram_message)
        await telegram_client.start_polling()
        logger.info("Telegram polling started with message handler")

        # Send a one-time agent-generated greeting now that Telegram + IPC are wired.
        asyncio.create_task(_send_telegram_connected_message(telegram_client, client))

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

    yield

    # Graceful Shutdown (OPS-002) - now using enhanced manager
    logger.info("Initiating graceful shutdown...")

    # Use the enhanced graceful shutdown manager
    await graceful_shutdown.initiate_shutdown()

    logger.info("Backend shutdown complete")


app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    lifespan=lifespan,
)

app.include_router(ui_router)

# Note: This backend is API-only. Frontend is served separately via npm/vite.
# The frontend proxies /api, /v1, /health requests to this backend.
logger.info("Backend running in API-only mode (no static file serving)")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_tracking_middleware(request: Request, call_next):
    """Track active requests for graceful shutdown (OPS-002)."""
    # Skip tracking for health/liveness probes
    if request.url.path in ("/health", "/ready", "/live"):
        return await call_next(request)

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
    # Skip rate limiting for health checks, UI endpoints, and if disabled
    # Health endpoints should never be rate-limited to allow monitoring
    # UI endpoints are for the frontend and don't hit the LLM
    health_paths = ("/health", "/ready", "/live", "/docs", "/openapi.json")
    if not config.rate_limit_enabled:
        return await call_next(request)
    if request.url.path in health_paths:
        return await call_next(request)
    if request.url.path.startswith("/api/ui/"):
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


# Track server start time for uptime calculation
_server_start_time = time.time()
_last_activity_time = time.time()


def _update_activity():
    """Update last activity timestamp."""
    global _last_activity_time
    _last_activity_time = time.time()


@app.get("/health")
async def health_check():
    """
    Simple health check endpoint.

    Returns minimal status - use SSE /api/ui/events for detailed status.
    """
    return {
        "status": "healthy" if not shutdown_manager.is_shutting_down else "draining",
    }


async def health_check_detailed():
    """
    Internal detailed health check for use by other modules.

    Returns comprehensive information about:
    - Server status and configuration
    - Progenitor client status
    - Rate limiter statistics
    - Shutdown status
    - Memory usage (if available)
    """
    import sys
    now = time.time()

    # Basic health info
    health_info = {
        "status": "healthy" if not shutdown_manager.is_shutting_down else "draining",
        "version": config.api_version,
        "uptime_seconds": round(now - _server_start_time, 1),
        "last_activity_seconds_ago": round(now - _last_activity_time, 1),
    }

    # Progenitor client status
    progenitor_status = {
        "running": client.is_running if client else False,
        "client_type": type(client).__name__ if client else None,
    }

    # Try to get more detailed Progenitor info
    if client and hasattr(client, "get_status"):
        try:
            details = client.get_status()
            if inspect.isawaitable(details):
                details = await details
            progenitor_status["details"] = details
        except Exception as e:
            logger.debug("Failed to get progenitor status details: %s", e)

    health_info["progenitor"] = progenitor_status

    # Rate limiter stats
    if config.rate_limit_enabled:
        health_info["rate_limiter"] = rate_limiter.get_stats()
    else:
        health_info["rate_limiter"] = {"enabled": False}

    # Shutdown status
    health_info["shutdown"] = shutdown_manager.get_status()

    # Memory usage (if psutil available)
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        health_info["memory"] = {
            "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
            "percent": round(process.memory_percent(), 2),
        }
    except ImportError:
        health_info["memory"] = {"available": False}
    except Exception as e:
        health_info["memory"] = {"error": str(e)}

    # Python info
    health_info["python"] = {
        "version": sys.version,
        "platform": sys.platform,
    }

    # Configuration summary (non-sensitive)
    health_info["config"] = {
        "model_name": config.model_name,
        "host": config.host,
        "port": config.port,
        "rate_limit_enabled": config.rate_limit_enabled,
        "rate_limit_per_minute": config.rate_limit_per_minute,
        "rate_limit_per_hour": config.rate_limit_per_hour,
    }

    return health_info


@app.get("/ready")
async def readiness_check():
    """
    Kubernetes-style readiness probe.

    Returns 200 if the server is ready to accept requests.
    Returns 503 if the server is shutting down or not ready.
    """
    if shutdown_manager.is_shutting_down:
        raise HTTPException(status_code=503, detail="Server is shutting down")

    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    return {"status": "ready"}


@app.get("/live")
async def liveness_check():
    """
    Kubernetes-style liveness probe.

    Returns 200 if the server process is alive.
    Always returns success unless the process is completely broken.
    """
    return {"status": "alive"}




@app.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    """List available models (OpenAI-compatible)."""
    return ModelsResponse(
        data=[
            ModelInfo(
                id=config.model_name,
                created=int(time.time()),
                owned_by="progenitor",
            )
        ]
    )


@app.post("/v1/responses", response_model=ResponsePayload)
async def responses(request: Request, body: ResponseRequest):
    """Unified Responses API (preferred).

    Accepts Responses `input` payloads only.
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    # Get request ID for correlation
    request_id = getattr(request.state, "request_id", None)

    try:
        last_user_msg, all_inputs = _extract_response_input(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        if body.stream:
            # CR-002: Validate request for streaming
            try:
                StreamingResponseRequest.validate_for_streaming(body)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            return StreamingResponse(
                stream_response_unified(last_user_msg, body.model, request_id),
                media_type="text/event-stream",
            )

        response_text = await client.send_message(last_user_msg, request_id=request_id)
        response_id = f"resp-{uuid.uuid4().hex[:8]}"

        return ResponsePayload(
            id=response_id,
            model=body.model,
            output=[
                ResponseOutputMessage(
                    content=[ResponseOutputText(text=response_text)]
                )
            ],
            usage=Usage(
                prompt_tokens=len(" ".join(all_inputs).split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(" ".join(all_inputs).split()) + len(response_text.split()),
            ),
        )

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Responses API error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _extract_response_input(request: ResponseRequest) -> tuple[str, list[str]]:
    """Normalize Responses API input to a single user string."""
    inputs: list[str] = []

    raw_input = request.input
    if isinstance(raw_input, str):
        inputs.append(raw_input)
    else:
        for item in raw_input:
            if isinstance(item, str):
                inputs.append(item)
            else:
                inputs.append(item.content or "")

    if not inputs:
        raise ValueError("No input provided")

    return inputs[-1], inputs


async def stream_response_unified(
    message: str,
    model: str,
    request_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream SSE events using the Responses API shape."""
    assert client is not None, "Client must be initialized"
    response_id = f"resp-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in client.send_message_stream(message, request_id=request_id):
            delta_event = {
                "id": response_id,
                "object": "response.output_text.delta",
                "model": model,
                "output_index": 0,
                "delta": {
                    "type": "output_text.delta",
                    "text": chunk,
                },
            }
            yield "event: response.output_text.delta\n" + f"data: {json.dumps(delta_event)}\n\n"

        completed_event = {
            "id": response_id,
            "object": "response.completed",
            "model": model,
        }
        yield "event: response.completed\n" + f"data: {json.dumps(completed_event)}\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)
        error_event = {"error": str(e)}
        yield "event: error\n" + f"data: {json.dumps(error_event)}\n\n"


# Simple chat endpoint (non-OpenAI format)
@app.post("/chat")
async def simple_chat(body: SimpleChatRequest):
    """Simple chat endpoint for quick testing (CR-002: now with Pydantic validation).

    Request: {"message": "your message here"}
    Response: {"response": "progenitor's response"}
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    try:
        response = await client.send_message(body.message)
        return {"response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Attachment Endpoints
# ============================================================================

@app.post("/v1/attachments")
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
):
    """
    Upload a file attachment for use in conversations.

    Returns attachment metadata including extracted text content.
    """
    content = await file.read()

    result = await create_attachment_from_upload(
        file_content=content,
        filename=file.filename or "uploaded_file",
        content_type=file.content_type or "application/octet-stream",
        manager=attachment_manager,
    )

    if result.error:
        raise HTTPException(status_code=400, detail=result.error)

    # Store in session if available
    session_id = request.cookies.get("progenitor_session")
    if session_id:
        session = await session_manager.get_session(session_id)
        if session:
            session.pending_attachments.append(result.id)

    return {
        "id": result.id,
        "name": result.name,
        "filename": result.original_filename,
        "mime_type": result.mime_type,
        "size_bytes": result.size_bytes,
        "content_truncated": result.content_truncated,
        "content_preview": result.content[:500] + "..." if len(result.content) > 500 else result.content,
    }


@app.get("/v1/attachments/{attachment_id}")
async def get_attachment(attachment_id: str):
    """Get attachment metadata and content by ID."""
    # Search in cache by ID
    for result in attachment_manager._cache.values():
        if result.id == attachment_id:
            return {
                "id": result.id,
                "name": result.name,
                "filename": result.original_filename,
                "mime_type": result.mime_type,
                "size_bytes": result.size_bytes,
                "content": result.content,
                "content_truncated": result.content_truncated,
                "metadata": result.metadata,
            }

    raise HTTPException(status_code=404, detail="Attachment not found")


# ============================================================================
# Session Endpoints
# ============================================================================

@app.get("/v1/session")
async def get_session(request: Request):
    """Get current session state."""
    session = await get_session_from_request(request, session_manager)
    return {
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "is_authenticated": session.is_authenticated,
        "preferences": session.preferences.model_dump(),
        "message_count": session.message_count,
        "pending_attachments": session.pending_attachments,
    }


@app.post("/v1/session")
async def create_new_session(request: Request, response: Response):
    """Create a new session."""
    session = await session_manager.create_session()
    set_session_cookie(response, session)
    return {
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
    }


@app.patch("/v1/session/preferences")
async def update_session_preferences(request: Request, body: dict):
    """Update session preferences."""
    session_id = request.cookies.get("progenitor_session")
    if not session_id:
        raise HTTPException(status_code=400, detail="No session found")

    session = await session_manager.update_preferences(session_id, **body)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"preferences": session.preferences.model_dump()}


@app.get("/v1/session/settings/export")
async def export_settings(request: Request):
    """Export session settings as YAML."""
    session = await get_session_from_request(request, session_manager)
    yaml_content = SettingsManager.export_settings(
        session.preferences,
        session.extension_states,
    )
    return Response(
        content=yaml_content,
        media_type="application/x-yaml",
        headers={"Content-Disposition": "attachment; filename=settings.yaml"},
    )


@app.post("/v1/session/settings/import")
async def import_settings(request: Request, response: Response):
    """Import settings from YAML."""
    body = await request.body()
    yaml_content = body.decode("utf-8")

    try:
        preferences, extension_states = SettingsManager.import_settings(yaml_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    session = await get_session_from_request(request, session_manager)
    session.preferences = preferences
    session.extension_states = extension_states
    set_session_cookie(response, session)

    return {"preferences": preferences.model_dump()}


# ============================================================================
# Conversation History Endpoints
# ============================================================================

@app.get("/v1/conversation")
async def get_conversation():
    """Get current conversation history."""
    return {
        "message_count": len(conversation_history),
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content[:500] + "..." if len(msg.content) > 500 else msg.content,
                "timestamp": msg.timestamp.isoformat(),
                "has_attachments": len(msg.metadata.attachments) > 0,
                "has_tool_calls": len(msg.metadata.tool_calls) > 0,
            }
            for msg in conversation_history.internal
        ],
    }


@app.get("/v1/conversation/{message_id}")
async def get_conversation_message(message_id: str):
    """Get a specific message with full metadata."""
    msg = conversation_history.get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    return {
        "id": msg.id,
        "role": msg.role,
        "content": msg.content,
        "visible_content": msg.visible_content,
        "timestamp": msg.timestamp.isoformat(),
        "metadata": msg.metadata.model_dump(),
    }


@app.delete("/v1/conversation")
async def clear_conversation():
    """Clear conversation history."""
    conversation_history.clear()
    return {"status": "cleared"}


# ============================================================================
# Enhanced Health Endpoints
# ============================================================================

@app.get("/health/features")
async def health_features():
    """Get status of enhanced features."""
    return {
        "streaming": {
            "enabled": True,
            "stop_flag": False,
        },
        "session_manager": session_manager.get_stats(),
        "attachment_manager": attachment_manager.get_cache_stats(),
        "conversation": {
            "message_count": len(conversation_history),
        },
        "graceful_shutdown": graceful_shutdown.get_status(),
    }
