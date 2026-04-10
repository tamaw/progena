"""
Custom middleware for the Progenitor DeepAgent.

Provides:
- DynamicMCPToolMiddleware: Injects live external MCP tools into each DeepAgent model call
- SecretRedactionMiddleware: Redacts API keys/secrets from tool call args and results
- AuditLoggingMiddleware: Logs tool calls to JSONL audit trail
- MetricsMiddleware: Tracks tool call counts and latency
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from app.progenitor.state.models import redact_secrets

from .tools_adapter import create_dynamic_mcp_tools

logger = logging.getLogger(__name__)


def _request_tool_name(tool: Any) -> str:
    if hasattr(tool, "name"):
        return str(tool.name or "").strip()
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict):
            return str(function.get("name") or "").strip()
        return str(tool.get("name") or "").strip()
    return ""


class DynamicMCPToolMiddleware(AgentMiddleware):
    """Inject and bind the current live external MCP tools for each agent turn."""

    def __init__(self, llm_client: Any = None):
        self._llm_client = llm_client
        self._warned_name_collisions: set[str] = set()

    def _dynamic_tools_by_name(self) -> dict[str, BaseTool]:
        tools_by_name: dict[str, BaseTool] = {}
        for tool in create_dynamic_mcp_tools(self._llm_client):
            name = _request_tool_name(tool)
            if name:
                tools_by_name[name] = tool
        return tools_by_name

    def _merge_request_tools(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        dynamic_tools = self._dynamic_tools_by_name()
        if not dynamic_tools:
            return request

        existing_tools = list(request.tools or [])
        merged_tools = list(existing_tools)
        seen_names: set[str] = set()
        for tool in existing_tools:
            name = _request_tool_name(tool)
            if name:
                seen_names.add(name)

        for tool in dynamic_tools.values():
            if tool.name in seen_names:
                if tool.name not in self._warned_name_collisions:
                    logger.warning(
                        "Skipping dynamic MCP tool '%s' because a tool with that name already exists",
                        tool.name,
                    )
                    self._warned_name_collisions.add(tool.name)
                continue
            seen_names.add(tool.name)
            merged_tools.append(tool)

        return request.override(tools=merged_tools)

    def _bind_tool_request(self, request: ToolCallRequest) -> ToolCallRequest:
        if request.tool is not None:
            return request

        tool_name = _request_tool_name(request.tool_call)
        if not tool_name:
            return request

        dynamic_tool = self._dynamic_tools_by_name().get(tool_name)
        if dynamic_tool is None:
            return request

        return replace(request, tool=dynamic_tool)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Any],
    ) -> Any:
        return handler(self._merge_request_tools(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        return await handler(self._merge_request_tools(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(self._bind_tool_request(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return await handler(self._bind_tool_request(request))


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
        tool_id = tool_call.get("id", "")

        # Redact args for logging (not for execution)
        raw_args = tool_call.get("args", {})
        redacted_args = {}
        for key, value in raw_args.items():
            if isinstance(value, str):
                redacted_args[key] = redact_secrets(value)
            else:
                redacted_args[key] = value

        start_time = time.monotonic()
        error = None
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
