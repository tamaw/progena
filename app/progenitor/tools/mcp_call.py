"""
MCP Call - Call an MCP tool by name via LiteLLM MCP (tools/call).

This is a generic dispatch tool because MCP tool names can change at runtime.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any, cast

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPCallTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "mcp_call"

    @property
    def description(self) -> str:
        return "Call an MCP tool by name via LiteLLM MCP (tools/call)"

    @property
    def keywords(self) -> list[str]:
        return ["mcp call", "call mcp", "tool call"]

    async def execute(self, context: ToolContext) -> ToolResult:
        llm_client = context.llm_client
        if llm_client is None:
            return ToolResult.fail("llm_client unavailable")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("Missing 'name' (MCP tool name)")

        arguments: Any = args.get("arguments") or {}
        if isinstance(arguments, str):
            raw = arguments.strip()
            if raw:
                with contextlib.suppress(Exception):
                    arguments = json.loads(raw)

        call = getattr(llm_client, "_call_mcp_tool", None)
        if not callable(call):
            return ToolResult.fail("MCP caller unavailable")

        result = await call({"name": name, "arguments": arguments})
        return ToolResult.ok(cast(dict[str, Any], result) if isinstance(result, dict) else {"result": result})

