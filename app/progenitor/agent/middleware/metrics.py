from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class MetricsMiddleware(AgentMiddleware):
    """Tracks tool call counts and latency for observability."""

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
        tool_name = request.tool_call.get("name", "unknown")
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
        start_time = time.monotonic()
        try:
            return await handler(request)
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
        return handler(request)

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        _ = (state, runtime)
        self._model_calls += 1
        return None

    def get_metrics(self) -> dict[str, Any]:
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
        self._call_counts.clear()
        self._total_duration_ms.clear()
        self._error_counts.clear()
        self._model_calls = 0
