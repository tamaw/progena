from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.progenitor.security.redaction import redact_secrets

logger = logging.getLogger(__name__)


class SecretRedactionMiddleware(AgentMiddleware):
    """Redacts secrets from tool call arguments and results."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        redacted_args = {}
        for key, value in tool_call.get("args", {}).items():
            redacted_args[key] = redact_secrets(value) if isinstance(value, str) else value

        if redacted_args != tool_call.get("args", {}):
            logger.debug(
                "Redacted secrets in tool call args for %s",
                tool_call.get("name", "unknown"),
            )
        return await handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return handler(request)
