from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.ipc.stream_events import (
    build_tool_error_event,
    build_tool_result_event,
    build_tool_start_event,
)
from app.progenitor.state.models import redact_secrets

logger = logging.getLogger(__name__)


def _emit_stream_event(payload: dict[str, Any]) -> None:
    from app.progenitor.agent import middleware as middleware_exports

    middleware_exports.emit_stream_event(payload)


class AuditLogSink(Protocol):
    async def write_log(
        self,
        *,
        source: str,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        timestamp: Any = None,
        relative_path: str | None = None,
    ) -> None: ...


class AuditLoggingMiddleware(AgentMiddleware):
    """Logs tool calls to the configured Postgres audit trail."""

    def __init__(self, log_sink: AuditLogSink | None = None):
        self._log_sink = log_sink

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "unknown")
        tool_id = tool_call.get("id", "") or ""
        raw_args = tool_call.get("args", {})
        redacted_args = {
            key: redact_secrets(value) if isinstance(value, str) else value
            for key, value in raw_args.items()
        }

        _emit_stream_event(build_tool_start_event(tool_name, tool_id, raw_args))

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
            if error is None:
                _emit_stream_event(build_tool_result_event(tool_name, tool_id, duration_ms, result))
            else:
                _emit_stream_event(build_tool_error_event(tool_name, tool_id, duration_ms, error))
            if self._log_sink is None:
                logger.warning("No audit log sink configured; dropping tool audit entry")
            else:
                try:
                    await self._log_sink.write_log(
                        source="tool_calls",
                        message=f"Tool call: {tool_name}",
                        level="error" if error else "info",
                        payload=entry,
                        timestamp=entry["timestamp"],
                        relative_path="tool_calls",
                    )
                except Exception:
                    logger.warning("Failed to write audit log to Postgres", exc_info=True)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)
