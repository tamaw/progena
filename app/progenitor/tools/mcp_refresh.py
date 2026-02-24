"""
MCP Refresh - Refresh MCP tool list from LiteLLM and optionally persist to registry.json.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, cast

from ..config import MCPConfig
from .mcp import setup_mcp_for_llm_client
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPRefreshTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "mcp_refresh"

    @property
    def description(self) -> str:
        return "Refresh MCP tool list from LiteLLM and optionally persist to registry.json"

    @property
    def keywords(self) -> list[str]:
        return ["mcp refresh", "refresh mcp", "reload mcp"]

    async def execute(self, context: ToolContext) -> ToolResult:
        llm_client = context.llm_client
        if llm_client is None:
            return ToolResult.fail("llm_client unavailable")

        mcp_config = getattr(llm_client, "mcp_config", None)
        if not isinstance(mcp_config, MCPConfig) or not mcp_config.enabled:
            return ToolResult.fail("MCP integration disabled")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        persist = bool(args.get("persist", True))

        await setup_mcp_for_llm_client(llm_client, mcp_config)

        tool_registry = context.tool_registry
        if persist and tool_registry and hasattr(tool_registry, "register_tool"):
            entries = getattr(llm_client, "get_mcp_tool_registry_entries", None)
            if callable(entries):
                with contextlib.suppress(Exception):
                    raw_entries = entries()
                    for entry in cast(list[dict[str, Any]], raw_entries or []):
                        await tool_registry.register_tool(entry)

        return ToolResult.ok({
            "success": True,
            "tool_count": len(getattr(llm_client, "mcp_tools", None) or []),
        })

