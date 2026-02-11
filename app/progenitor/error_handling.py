"""
Error Handling Policy and Utilities for Progenitor.

CQ-007: Standardized error handling pattern.

This module documents and enforces the error handling policy:

1. Use custom exceptions from nodes/errors.py
2. RecoverableError for transient issues (rate limits, timeouts)
3. FatalError for config/setup issues
4. Always log before suppressing
5. Include context dict with all exceptions

Error Hierarchy:
    NodeError (base)
    ├── RecoverableError
    │   └── LLMError
    ├── FatalError
    ├── ValidationError
    └── ToolError

Usage:
    from app.progenitor.error_handling import (
        handle_recoverable,
        handle_with_fallback,
        ErrorContext,
    )

    async with handle_recoverable(logger, "operation"):
        await risky_operation()
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from enum import StrEnum
from typing import Any, TypeVar

from .nodes.errors import (
    FatalError,
    LLMError,
    NodeError,
    RecoverableError,
    ToolError,
    ValidationError,
)

T = TypeVar("T")


class ErrorSeverity(StrEnum):
    """Error severity classification."""
    RECOVERABLE = "recoverable"
    FATAL = "fatal"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


def classify_error(error: Exception) -> ErrorSeverity:
    """Classify an error by severity.

    Args:
        error: The exception to classify.

    Returns:
        ErrorSeverity indicating how to handle the error.
    """
    # Network/timeout errors are recoverable
    if isinstance(error, (asyncio.TimeoutError, ConnectionError, ConnectionRefusedError, ConnectionResetError)):
        return ErrorSeverity.RECOVERABLE

    # Our custom errors
    if isinstance(error, RecoverableError):
        return ErrorSeverity.RECOVERABLE
    if isinstance(error, FatalError):
        return ErrorSeverity.FATAL

    # OS errors that might be transient
    if isinstance(error, OSError) and error.errno in (28, 11):  # No space, try again
        return ErrorSeverity.TRANSIENT

    return ErrorSeverity.UNKNOWN

# Re-export errors for convenience
__all__ = [
    # Errors
    "NodeError",
    "RecoverableError",
    "FatalError",
    "ValidationError",
    "LLMError",
    "ToolError",
    # Classification
    "ErrorSeverity",
    "classify_error",
    # Utilities
    "handle_recoverable",
    "handle_with_fallback",
    "ErrorContext",
    "wrap_recoverable",
    "log_and_raise",
]


class ErrorContext:
    """Context builder for error metadata.

    Usage:
        context = ErrorContext(node="decide", user_input="hello")
        raise LLMError("API failed", context=context.to_dict())
    """

    def __init__(self, **fields: Any):
        """Initialize with context fields."""
        self._fields = fields

    def add(self, key: str, value: Any) -> "ErrorContext":
        """Add a context field."""
        self._fields[key] = value
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for exception context."""
        return dict(self._fields)


@asynccontextmanager
async def handle_recoverable(
    logger: logging.Logger,
    operation: str,
    context: dict[str, Any] | None = None,
    suppress: bool = False,
    on_error: Callable[[Exception], Any] | None = None,
):
    """Context manager for handling recoverable errors.

    Args:
        logger: Logger for error logging.
        operation: Name of the operation for logging.
        context: Additional context for logging.
        suppress: If True, suppress RecoverableError and log as warning.
        on_error: Optional callback on error.

    Raises:
        RecoverableError: If suppress=False and a recoverable error occurs.
        FatalError: Always re-raised.

    Usage:
        async with handle_recoverable(logger, "api_call"):
            await make_api_call()
    """
    try:
        yield
    except RecoverableError as e:
        # Log with context
        log_context = {
            "operation": operation,
            "error_type": type(e).__name__,
            **(context or {}),
            **(e.context or {}),
        }

        if suppress:
            logger.warning(
                "%s failed (recoverable, suppressed): %s",
                operation,
                e,
                extra=log_context,
            )
            if on_error:
                on_error(e)
        else:
            logger.error(
                "%s failed (recoverable): %s",
                operation,
                e,
                extra=log_context,
            )
            raise
    except FatalError:
        # Always re-raise fatal errors
        raise
    except Exception as e:
        # Unexpected error - log and re-raise
        logger.error(
            "%s failed (unexpected): %s",
            operation,
            e,
            extra={"operation": operation, **(context or {})},
            exc_info=True,
        )
        raise


@contextmanager
def handle_with_fallback(
    logger: logging.Logger,
    operation: str,
    fallback: Any,
    context: dict[str, Any] | None = None,
):
    """Context manager that returns fallback on error.

    Args:
        logger: Logger for error logging.
        operation: Name of the operation.
        fallback: Value to use on error.
        context: Additional context.

    Yields:
        A result container that holds the result or fallback.

    Usage:
        class Result:
            value: Any = None
            error: Optional[Exception] = None

        with handle_with_fallback(logger, "parse", fallback={}) as result:
            result.value = json.loads(data)

        # result.value is either parsed data or {}
    """
    class ResultContainer:
        value: Any = None
        error: Exception | None = None

    result = ResultContainer()

    try:
        yield result
    except Exception as e:
        result.error = e
        result.value = fallback

        logger.warning(
            "%s failed, using fallback: %s",
            operation,
            e,
            extra={"operation": operation, **(context or {})},
        )


def wrap_recoverable(
    logger: logging.Logger,
    operation: str,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    exponential_backoff: bool = True,
):
    """Decorator for wrapping async functions with retry logic.

    Args:
        logger: Logger for error logging.
        operation: Name of the operation.
        max_retries: Maximum retry attempts.
        retry_delay: Base delay between retries.
        exponential_backoff: Whether to use exponential backoff.

    Returns:
        Decorator function.

    Usage:
        @wrap_recoverable(logger, "api_call", max_retries=3)
        async def make_api_call():
            ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            delay = retry_delay

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)  # type: ignore[misc]
                except RecoverableError as e:
                    last_error = e

                    if attempt < max_retries - 1:
                        logger.warning(
                            "%s failed (attempt %d/%d): %s. Retrying in %.1fs...",
                            operation,
                            attempt + 1,
                            max_retries,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)

                        if exponential_backoff:
                            delay *= 2
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            operation,
                            max_retries,
                            e,
                        )
                except FatalError:
                    # Don't retry fatal errors
                    raise

            # All retries exhausted
            if last_error:
                raise last_error
            raise RuntimeError(f"{operation} failed without error")  # Should never happen

        return wrapper  # type: ignore
    return decorator


def log_and_raise(
    logger: logging.Logger,
    error: NodeError,
    level: int = logging.ERROR,
) -> None:
    """Log an error and re-raise it.

    Ensures errors are always logged before propagating.

    Args:
        logger: Logger for error logging.
        error: The error to log and raise.
        level: Logging level.

    Raises:
        The provided error.
    """
    extra = {
        "error_type": type(error).__name__,
        "node_name": error.node_name,
        **(error.context or {}),
    }

    logger.log(level, str(error), extra=extra, exc_info=True)
    raise error


def classify_error_type(error: Exception) -> str:
    """Classify an error for metrics/logging.

    Args:
        error: The exception.

    Returns:
        Classification string.
    """
    if isinstance(error, LLMError):
        return "llm_error"
    if isinstance(error, ValidationError):
        return "validation_error"
    if isinstance(error, ToolError):
        return "tool_error"
    if isinstance(error, RecoverableError):
        return "recoverable_error"
    if isinstance(error, FatalError):
        return "fatal_error"
    if isinstance(error, NodeError):
        return "node_error"
    if isinstance(error, asyncio.TimeoutError):
        return "timeout_error"
    if isinstance(error, ConnectionError):
        return "connection_error"
    return "unknown_error"
