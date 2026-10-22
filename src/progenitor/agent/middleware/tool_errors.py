"""Return failed tool calls to the model instead of ending the whole run.

LangGraph's ToolNode re-raises anything that is not a ToolException, so one
failing tool (e.g. a subagent that cannot be built) used to abort the chat
stream. LangGraph control flow (interrupts, Command bubbling) must still
propagate, so ``GraphBubbleUp`` is re-raised; langchain's ToolRetryMiddleware
is not used because it would swallow those too.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from src.progenitor.security.redaction import redact_secrets

logger = logging.getLogger(__name__)


def _error_message(request: ToolCallRequest, exc: Exception) -> ToolMessage:
    name = request.tool.name if request.tool else request.tool_call["name"]
    logger.exception("Tool %s failed", name)
    return ToolMessage(
        content=redact_secrets(f"Tool '{name}' failed: {type(exc).__name__}: {exc}"),
        tool_call_id=request.tool_call["id"],
        name=name,
        status="error",
    )


class ToolErrorMiddleware(AgentMiddleware):
    """Convert tool exceptions into error ToolMessages the model can act on."""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return _error_message(request, exc)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return _error_message(request, exc)
