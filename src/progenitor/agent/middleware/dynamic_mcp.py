"""Dynamic MCP tool injection middleware.

Injects and binds the current live external MCP tools for each agent turn.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

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

    def __init__(self, mcp_tools_provider: Callable[[], list[BaseTool]] | None = None):
        self._mcp_tools_provider = mcp_tools_provider or (lambda: [])
        self._warned_name_collisions: set[str] = set()

    def _dynamic_tools_by_name(self) -> dict[str, BaseTool]:
        tools_by_name: dict[str, BaseTool] = {}
        for tool in self._mcp_tools_provider():
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
