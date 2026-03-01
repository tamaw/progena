"""Local DeepAgent middleware for Builder Copilot."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    re.compile(r"(ghp_[A-Za-z0-9]{20,})"),
    re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(sk-[A-Za-z0-9]{20,})"),
]


def _redact_secret_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED***", redacted)
    return redacted


def _redact_object(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_secret_text(value)
    if isinstance(value, dict):
        return {key: _redact_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_object(item) for item in value]
    return value


class SecretRedactionMiddleware(AgentMiddleware):
    """Redact secrets from tool call logging surfaces."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        args = request.tool_call.get("args", {})
        redacted_args = _redact_object(args)
        if redacted_args != args:
            logger.debug("Redacted sensitive tool args for %s", request.tool_call.get("name", "unknown"))
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)


class AuditLoggingMiddleware(AgentMiddleware):
    """Write tool call audit entries to a JSONL file."""

    def __init__(self, log_dir: str | Path | None = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        start = time.monotonic()
        error: str | None = None
        try:
            return await handler(request)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            entry = {
                "timestamp": time.time(),
                "tool_name": request.tool_call.get("name", "unknown"),
                "tool_id": request.tool_call.get("id", ""),
                "args": _redact_object(request.tool_call.get("args", {})),
                "duration_ms": duration_ms,
                "success": error is None,
                "error": error,
            }
            logger.info("Tool call: %s (%.1fms)", entry["tool_name"], duration_ms)
            if self._log_dir:
                try:
                    with (self._log_dir / "tool_calls.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry) + "\n")
                except OSError:
                    logger.warning("Failed to write Builder Copilot audit log", exc_info=True)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)


class MetricsMiddleware(AgentMiddleware):
    """Track simple tool and model call counters."""

    def __init__(self) -> None:
        self._tool_calls = 0
        self._model_calls = 0

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        self._tool_calls += 1
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        del state
        del runtime
        self._model_calls += 1
        return None
