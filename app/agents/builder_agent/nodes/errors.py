"""
Custom exceptions for Progenitor nodes.

Provides a hierarchy of errors to distinguish between:
- RecoverableError: Transient issues that can be retried
- FatalError: Permanent failures that should stop processing
- ValidationError: Input/output validation failures
"""

from typing import Any


class NodeError(Exception):
    """Base exception for all node errors."""

    def __init__(
        self,
        message: str,
        node_name: str | None = None,
        context: dict[str, Any] | None = None,
    ):
        self.node_name = node_name
        self.context = context or {}
        super().__init__(message)

    def __str__(self) -> str:
        prefix = f"[{self.node_name}] " if self.node_name else ""
        return f"{prefix}{super().__str__()}"


class RecoverableError(NodeError):
    """
    Transient error that can be retried.

    Examples:
    - Rate limit exceeded
    - Temporary network failure
    - LLM timeout
    """

    def __init__(
        self,
        message: str,
        node_name: str | None = None,
        context: dict[str, Any] | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message, node_name, context)
        self.retry_after_seconds = retry_after_seconds


class FatalError(NodeError):
    """
    Permanent error that should not be retried.

    Examples:
    - Invalid configuration
    - Missing required credentials
    - Corrupt state file
    """
    pass


class ValidationError(NodeError):
    """
    Input or output validation failure.

    Examples:
    - Invalid work item format
    - Schema validation failure
    - Missing required fields
    """

    def __init__(
        self,
        message: str,
        node_name: str | None = None,
        context: dict[str, Any] | None = None,
        field_errors: dict[str, str] | None = None,
    ):
        super().__init__(message, node_name, context)
        self.field_errors = field_errors or {}


class LLMError(RecoverableError):
    """Error from LLM API calls (usually recoverable with retry)."""
    pass


class ToolError(NodeError):
    """Error from tool execution (may or may not be recoverable)."""

    def __init__(
        self,
        message: str,
        tool_name: str,
        node_name: str | None = None,
        context: dict[str, Any] | None = None,
        is_recoverable: bool = True,
    ):
        super().__init__(message, node_name, context)
        self.tool_name = tool_name
        self.is_recoverable = is_recoverable


class StateError(FatalError):
    """Error with state management (usually fatal)."""
    pass


class ConfigurationError(FatalError):
    """Configuration error (always fatal)."""
    pass
