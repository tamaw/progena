"""
Chat Server Main - FastAPI application with OpenAI-compatible interface.

This provides a standardized chat API that proxies to the Progenitor agent.

Communication modes:
- IPC (default): Socket-based communication for reliability and streaming
- Subprocess: Legacy stdin/stdout mode (deprecated)
- In-process: Direct service calls (for testing)

Features (from text-generation-webui patterns):
- Streaming callbacks for real-time token streaming
- Conversation metadata with reasoning traces
- File attachments for document-grounded chat
- Session management for state persistence
- Graceful shutdown with request draining
"""

import asyncio
import logging
import json
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Union

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .config import ChatServerConfig
from .client import ProgenitorClient, get_client
from .ipc_client import IPCProgenitorClient, get_ipc_client
from .models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatMessage,
    Usage,
    ModelsResponse,
    ModelInfo,
    ErrorResponse,
    ErrorDetail,
    ResponseRequest,
    ResponsePayload,
    ResponseOutputMessage,
    ResponseOutputText,
    SimpleChatRequest,
    StreamingChatRequest,
    StreamingResponseRequest,
)

# New modules for enhanced features
from .streaming import (
    Iteratorize,
    AsyncIteratorize,
    StreamingBuffer,
    stream_with_callback,
    stop_everything,
    reset_stop_flag,
)
from .conversation import (
    ConversationHistory,
    ConversationMessage as ConvMessage,
    MessageMetadata,
    AttachmentType,
    truncate_content_by_tokens,
)
from .attachments import (
    AttachmentManager,
    AttachmentConfig,
    AttachmentResult,
    format_attachments_for_context,
    create_attachment_from_upload,
)
from .session import (
    SessionManager,
    SessionConfig,
    SessionState,
    UserPreferences,
    SettingsManager,
    get_session_from_request,
    set_session_cookie,
)
from .shutdown import (
    GracefulShutdownManager,
    ShutdownPhase,
    managed_shutdown,
    stop_client_safely,
    commit_state_safely,
)

logger = logging.getLogger(__name__)

# Global config and client
config = ChatServerConfig.from_env()
# Client can be either IPC-based or subprocess-based
client: Union[ProgenitorClient, IPCProgenitorClient, None] = None

# Enhanced feature managers
session_manager: SessionManager = SessionManager(SessionConfig(
    persist_dir=Path("state/sessions") if config.host else None,
))
attachment_manager: AttachmentManager = AttachmentManager(AttachmentConfig(
    cache_dir=Path("state/attachments"),
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

    MAX_TRACKED_CLIENTS = 10000  # Maximum clients to track (memory protection)

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
            removed = self._cleanup_stale_clients(force=True)

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
    cleanup_interval=config.rate_limit_cleanup_interval,
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
        except asyncio.TimeoutError:
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
    logger.info("Starting chat server with enhanced features...")

    # Initialize session manager
    await session_manager.start()
    logger.info("Session manager started")

    # Install graceful shutdown signal handlers
    graceful_shutdown.install_signal_handlers()

    # Register shutdown callbacks
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

    # Choose client based on configuration
    if config.progenitor_ipc_enabled:
        logger.info(
            "Using IPC client (host=%s, port=%d, auto_start=%s)",
            config.progenitor_ipc_host,
            config.progenitor_ipc_port,
            config.progenitor_ipc_auto_start,
        )
        client = get_ipc_client(config)
    else:
        logger.info("Using subprocess client (legacy mode)")
        client = get_client(config)

    # Register client shutdown callback
    if client:
        graceful_shutdown.register_callback(
            "stop_progenitor_client",
            lambda: stop_client_safely(client, timeout=10.0, name="Progenitor"),
            priority=30,
            timeout=15.0,
        )

    # Don't auto-start Progenitor - let it start on first request
    # This allows the server to start even if Progenitor has issues
    logger.info("Chat server ready on %s:%d", config.host, config.port)
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

    logger.info("Chat server shutdown complete")


app = FastAPI(
    title=config.api_title,
    version=config.api_version,
    lifespan=lifespan,
)

# Include UI API routes
from .ui_routes import router as ui_router
app.include_router(ui_router)

# Mount static files for the Chat UI
_chat_ui_path = Path(__file__).parent.parent / "chat-ui"
if _chat_ui_path.exists():
    app.mount("/css", StaticFiles(directory=_chat_ui_path / "css"), name="ui-css")
    app.mount("/js", StaticFiles(directory=_chat_ui_path / "js"), name="ui-js")

    @app.get("/")
    async def serve_ui():
        """Serve the Chat UI at root."""
        return FileResponse(_chat_ui_path / "index.html")

    logger.info("Chat UI mounted at /")
else:
    logger.warning("Chat UI not found at %s", _chat_ui_path)

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
    if request.url.path in ("/health", "/health/detailed", "/ready", "/live"):
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
    health_paths = ("/health", "/health/detailed", "/ready", "/live", "/docs", "/openapi.json")
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


@app.get("/health/detailed", deprecated=True)
async def health_check_detailed():
    """
    Detailed health check endpoint with full diagnostics (OPS-001).

    DEPRECATED: Use SSE endpoint /api/ui/events for real-time status updates.
    This endpoint will be removed in a future version.

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
            progenitor_status["details"] = client.get_status()
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


@app.get("/metrics")
async def metrics():
    """
    Prometheus-compatible metrics endpoint.

    Returns metrics in Prometheus text format for scraping.
    """
    try:
        from ..progenitor.telemetry import get_metrics

        collector = get_metrics()
        prometheus_text = collector.to_prometheus()

        return Response(
            content=prometheus_text,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    except ImportError:
        return Response(
            content="# Telemetry not available\n",
            media_type="text/plain",
        )
    except Exception as e:
        logger.warning("Failed to collect metrics: %s", e)
        return Response(
            content=f"# Error collecting metrics: {e}\n",
            media_type="text/plain",
        )


@app.get("/metrics/json")
async def metrics_json():
    """
    JSON metrics endpoint for programmatic access.

    Returns all metrics as JSON objects.
    """
    try:
        from ..progenitor.telemetry import get_metrics

        collector = get_metrics()
        points = collector.collect()

        return {
            "metrics": [p.to_dict() for p in points],
            "timestamp": time.time(),
        }
    except ImportError:
        return {"error": "Telemetry not available", "metrics": []}
    except Exception as e:
        logger.warning("Failed to collect metrics: %s", e)
        return {"error": str(e), "metrics": []}


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

    Accepts either the new Responses `input` shape or the legacy
    Chat Completions `messages` for compatibility.
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


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(request: Request, body: ChatCompletionRequest):
    """Legacy Chat Completions API.

    Kept for backwards compatibility but internally powered by
    the same engine as /v1/responses.
    """
    if not client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    # Get request ID for correlation
    request_id = getattr(request.state, "request_id", None)

    try:
        last_user_msg = _extract_chat_message(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        if body.stream:
            # CR-002: Validate request for streaming
            try:
                StreamingChatRequest.validate_for_streaming(body)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            return StreamingResponse(
                stream_response(last_user_msg, body.model, request_id),
                media_type="text/event-stream",
            )

        response_text = await client.send_message(last_user_msg, request_id=request_id)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

        return ChatCompletionResponse(
            id=completion_id,
            model=body.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=response_text,
                    ),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=len(last_user_msg.split()),
                completion_tokens=len(response_text.split()),
                total_tokens=len(last_user_msg.split()) + len(response_text.split()),
            ),
        )

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        logger.error(f"Chat completion error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def stream_response(
    message: str,
    model: str,
    request_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream response chunks in SSE format."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    try:
        async for chunk in client.send_message_stream(message, request_id=request_id):
            chunk_data = ChatCompletionChunk(
                id=completion_id,
                model=model,
                choices=[
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }
                ],
            )
            yield f"data: {chunk_data.model_dump_json()}\n\n"

        # Send final chunk with finish_reason
        final_chunk = ChatCompletionChunk(
            id=completion_id,
            model=model,
            choices=[
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Streaming error: {e}", exc_info=True)
        error_chunk = {"error": str(e)}
        yield f"data: {error_chunk}\n\n"


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
                inputs.append(item.content)

    if not inputs and request.messages:
        inputs.extend([msg.content for msg in request.messages if msg.role in ("user", "system")])

    if not inputs:
        raise ValueError("No input provided")

    return inputs[-1], inputs


def _extract_chat_message(request: ChatCompletionRequest) -> str:
    """Extract last user message from Chat Completions request."""
    if not request.messages:
        raise ValueError("No messages provided")

    user_messages = [
        f"{msg.role}: {msg.content}"
        for msg in request.messages
        if msg.role in ("user", "system")
    ]

    if not user_messages:
        raise ValueError("No user message found")

    return next(
        (msg.content for msg in reversed(request.messages) if msg.role == "user"),
        user_messages[-1],
    )


async def stream_response_unified(
    message: str,
    model: str,
    request_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream SSE events using the Responses API shape."""
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
