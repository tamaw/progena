"""
Structured Logging Utilities for Progenitor.

OBS-003: Provides structured logging with trace correlation.

This module provides:
- Structured logging with extra fields
- Trace ID correlation
- Context managers for logging contexts
- Type-safe log helpers
"""

import logging
import uuid
from contextvars import ContextVar
from typing import Any, MutableMapping, Optional

# Context variable for trace ID correlation
_trace_id: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)
_request_id: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def get_trace_id() -> Optional[str]:
    """Get the current trace ID from context."""
    return _trace_id.get()


def set_trace_id(trace_id: str) -> None:
    """Set the trace ID in context."""
    _trace_id.set(trace_id)


def get_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return _request_id.get()


def set_request_id(request_id: str) -> None:
    """Set the request ID in context."""
    _request_id.set(request_id)


def generate_trace_id() -> str:
    """Generate a new trace ID."""
    return f"trace_{uuid.uuid4().hex[:12]}"


def generate_request_id() -> str:
    """Generate a new request ID."""
    return f"req_{uuid.uuid4().hex[:12]}"


class StructuredLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that adds structured fields.

    Automatically includes:
    - trace_id: From context
    - request_id: From context
    - Any extra fields passed to log calls
    """

    def process(self, msg: str, kwargs: MutableMapping[str, Any]) -> tuple[str, MutableMapping[str, Any]]:
        """Process log record to add structured fields."""
        extra = kwargs.get("extra", {})

        # Add trace correlation
        if trace_id := get_trace_id():
            extra["trace_id"] = trace_id
        if request_id := get_request_id():
            extra["request_id"] = request_id

        # Merge with adapter's extra
        if self.extra:
            extra = {**self.extra, **extra}

        kwargs["extra"] = extra
        return msg, kwargs


def get_structured_logger(
    name: str,
    extra: Optional[dict[str, Any]] = None,
) -> StructuredLoggerAdapter:
    """Get a structured logger adapter.

    Args:
        name: Logger name (typically __name__).
        extra: Additional static fields to include in all logs.

    Returns:
        StructuredLoggerAdapter instance.
    """
    logger = logging.getLogger(name)
    return StructuredLoggerAdapter(logger, extra or {})


class LogContext:
    """Context manager for structured logging context.

    Sets trace_id and request_id for the duration of the context.

    Usage:
        with LogContext(request_id="req-123"):
            logger.info("Processing request")  # Includes request_id
    """

    def __init__(
        self,
        trace_id: Optional[str] = None,
        request_id: Optional[str] = None,
        generate_trace: bool = False,
    ):
        """Initialize log context.

        Args:
            trace_id: Trace ID to set.
            request_id: Request ID to set.
            generate_trace: If True, generate a new trace ID if not provided.
        """
        self._trace_id = trace_id
        self._request_id = request_id
        self._generate_trace = generate_trace
        self._old_trace_id: Optional[str] = None
        self._old_request_id: Optional[str] = None

    def __enter__(self) -> "LogContext":
        """Enter the context."""
        self._old_trace_id = get_trace_id()
        self._old_request_id = get_request_id()

        if self._trace_id:
            set_trace_id(self._trace_id)
        elif self._generate_trace and not self._old_trace_id:
            set_trace_id(generate_trace_id())

        if self._request_id:
            set_request_id(self._request_id)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the context, restoring previous values."""
        if self._old_trace_id is not None:
            set_trace_id(self._old_trace_id)
        if self._old_request_id is not None:
            set_request_id(self._old_request_id)


# Type-safe structured log helpers
def log_info(
    logger: logging.Logger,
    message: str,
    **fields: Any,
) -> None:
    """Log info with structured fields.

    Args:
        logger: The logger instance.
        message: Log message.
        **fields: Structured fields to include.
    """
    extra = _build_extra(fields)
    logger.info(message, extra=extra)


def log_warning(
    logger: logging.Logger,
    message: str,
    **fields: Any,
) -> None:
    """Log warning with structured fields."""
    extra = _build_extra(fields)
    logger.warning(message, extra=extra)


def log_error(
    logger: logging.Logger,
    message: str,
    error: Optional[Exception] = None,
    **fields: Any,
) -> None:
    """Log error with structured fields.

    Args:
        logger: The logger instance.
        message: Log message.
        error: Optional exception to include.
        **fields: Structured fields to include.
    """
    extra = _build_extra(fields)
    if error:
        extra["error"] = str(error)
        extra["error_type"] = type(error).__name__
    logger.error(message, extra=extra, exc_info=error is not None)


def log_debug(
    logger: logging.Logger,
    message: str,
    **fields: Any,
) -> None:
    """Log debug with structured fields."""
    extra = _build_extra(fields)
    logger.debug(message, extra=extra)


def _build_extra(fields: dict[str, Any]) -> dict[str, Any]:
    """Build extra dict with trace correlation."""
    extra = dict(fields)

    if trace_id := get_trace_id():
        extra["trace_id"] = trace_id
    if request_id := get_request_id():
        extra["request_id"] = request_id

    return extra


# JSON formatter for production logging
class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging.

    Outputs log records as JSON lines for easy parsing by
    log aggregation systems.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        import json
        from datetime import datetime

        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add location info
        if record.pathname:
            log_data["file"] = record.pathname
            log_data["line"] = str(record.lineno)

        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "filename",
                "funcName", "levelname", "levelno", "lineno",
                "module", "msecs", "pathname", "process",
                "processName", "relativeCreated", "stack_info",
                "exc_info", "exc_text", "thread", "threadName",
                "taskName", "message",
            ):
                log_data[key] = value

        return json.dumps(log_data, default=str)


def setup_json_logging(level: int = logging.INFO) -> None:
    """Setup JSON logging for production.

    Args:
        level: Logging level.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
