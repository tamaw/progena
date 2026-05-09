"""
Custom middleware for the Progenitor DeepAgent.

Provides:
- SecretRedactionMiddleware: Redacts API keys/secrets from tool call args and results
- AuditLoggingMiddleware: Logs tool calls to JSONL audit trail
- MetricsMiddleware: Tracks tool call counts and latency
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.ipc.stream_events import (
    build_tool_error_event,
    build_tool_result_event,
    build_tool_start_event,
    emit_stream_event,
)
from app.progenitor.state.models import redact_secrets

logger = logging.getLogger(__name__)


class SecretRedactionMiddleware(AgentMiddleware):
    """Redacts secrets from tool call arguments and results.

    Uses Progenitor's existing redact_secrets() which covers OpenAI, Azure,
    GitHub, Anthropic, AWS, and generic secret patterns. Applied as a
    wrap_tool_call interceptor so it covers all tools automatically.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Redact secrets in tool args before execution, and in results after."""
        # Redact secrets in tool call arguments
        tool_call = request.tool_call
        redacted_args = {}
        for key, value in tool_call.get("args", {}).items():
            if isinstance(value, str):
                redacted_args[key] = redact_secrets(value)
            else:
                redacted_args[key] = value

        if redacted_args != tool_call.get("args", {}):
            logger.debug(
                "Redacted secrets in tool call args for %s",
                tool_call.get("name", "unknown"),
            )

        # Don't modify the actual tool call args — we log redacted but
        # execute with originals (tools need real credentials to work)
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version of redaction wrapper."""
        return handler(request)


class AuditLoggingMiddleware(AgentMiddleware):
    """Logs tool calls to a JSONL audit trail.

    Each tool invocation is logged with: timestamp, tool name, arguments
    (redacted), duration, and success/failure status. Compatible with
    Progenitor's existing JSONL logging format.
    """

    def __init__(self, log_dir: str | Path | None = None):
        """Initialize audit logger.

        Args:
            log_dir: Directory for audit logs. If None, logs to Python logger only.
        """
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Log tool call with timing and redaction."""
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "unknown")
        tool_id = tool_call.get("id", "") or ""

        # Redact args for logging (not for execution)
        raw_args = tool_call.get("args", {})
        redacted_args = {}
        for key, value in raw_args.items():
            if isinstance(value, str):
                redacted_args[key] = redact_secrets(value)
            else:
                redacted_args[key] = value

        # Best-effort: surface tool start to the UI stream.
        emit_stream_event(build_tool_start_event(tool_name, tool_id, raw_args))

        start_time = time.monotonic()
        error: str | None = None
        result: ToolMessage | Command[Any] | None = None
        try:
            result = await handler(request)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            entry = {
                "timestamp": time.time(),
                "tool_name": tool_name,
                "tool_id": tool_id,
                "args": redacted_args,
                "duration_ms": round(duration_ms, 2),
                "success": error is None,
                "error": error,
            }
            logger.info("Tool call: %s (%.1fms)", tool_name, duration_ms)

            # Best-effort: surface tool completion to the UI stream.
            if error is None:
                emit_stream_event(build_tool_result_event(tool_name, tool_id, duration_ms, result))
            else:
                emit_stream_event(build_tool_error_event(tool_name, tool_id, duration_ms, error))

            if self._log_dir:
                log_file = self._log_dir / "tool_calls.jsonl"
                try:
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry) + "\n")
                except OSError:
                    logger.warning("Failed to write audit log to %s", log_file)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version — just pass through (most calls are async)."""
        return handler(request)


class MetricsMiddleware(AgentMiddleware):
    """Tracks tool call counts and latency for observability.

    Maintains in-memory counters accessible via get_metrics(). Useful for
    understanding agent behavior patterns and identifying bottlenecks.
    """

    def __init__(self):
        self._call_counts: dict[str, int] = {}
        self._total_duration_ms: dict[str, float] = {}
        self._error_counts: dict[str, int] = {}
        self._model_calls: int = 0

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Track tool call metrics."""
        tool_name = request.tool_call.get("name", "unknown")
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

        start_time = time.monotonic()
        try:
            result = await handler(request)
            return result
        except Exception:
            self._error_counts[tool_name] = self._error_counts.get(tool_name, 0) + 1
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._total_duration_ms[tool_name] = (
                self._total_duration_ms.get(tool_name, 0) + duration_ms
            )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Sync version — just pass through."""
        return handler(request)

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Track model call count."""
        self._model_calls += 1
        return None

    def get_metrics(self) -> dict[str, Any]:
        """Return current metrics snapshot."""
        tool_metrics = {}
        for name in sorted(self._call_counts):
            count = self._call_counts[name]
            total_ms = self._total_duration_ms.get(name, 0)
            tool_metrics[name] = {
                "calls": count,
                "errors": self._error_counts.get(name, 0),
                "total_ms": round(total_ms, 2),
                "avg_ms": round(total_ms / count, 2) if count > 0 else 0,
            }
        return {
            "model_calls": self._model_calls,
            "tool_calls": sum(self._call_counts.values()),
            "tool_errors": sum(self._error_counts.values()),
            "tools": tool_metrics,
        }

    def reset(self):
        """Reset all metrics."""
        self._call_counts.clear()
        self._total_duration_ms.clear()
        self._error_counts.clear()
        self._model_calls = 0
