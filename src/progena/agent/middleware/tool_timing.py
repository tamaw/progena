"""Tool timing and stream-event middleware."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.progena.runtime.streaming.stream_events import (
    build_tool_error_event,
    build_tool_result_event,
    build_tool_start_event,
)

logger = logging.getLogger(__name__)


def _emit_stream_event(payload: dict[str, Any]) -> None:
    from src.progena.agent import middleware as middleware_exports

    middleware_exports.emit_stream_event(payload)


class ToolTimingMiddleware(AgentMiddleware):
    """Emit tool lifecycle events and persist completed-call timing in messages."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "unknown")
        tool_id = tool_call.get("id", "") or ""
        raw_args = tool_call.get("args", {})
        _emit_stream_event(build_tool_start_event(tool_name, tool_id, raw_args))

        start_time = time.monotonic()
        try:
            result = await handler(request)
        except Exception as exc:
            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            logger.info("Tool call failed: %s (%.1fms)", tool_name, duration_ms)
            _emit_stream_event(build_tool_error_event(tool_name, tool_id, duration_ms, str(exc)))
            raise

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        logger.info("Tool call: %s (%.1fms)", tool_name, duration_ms)
        _emit_stream_event(build_tool_result_event(tool_name, tool_id, duration_ms, result))
        if isinstance(result, ToolMessage):
            return result.model_copy(
                update={
                    "additional_kwargs": {
                        **result.additional_kwargs,
                        "duration_ms": duration_ms,
                    }
                }
            )
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)
