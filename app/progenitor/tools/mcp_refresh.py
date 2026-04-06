"""
MCP Refresh - Refresh live MCP tool discovery from app/progenitor/mcp.json.
"""

import logging

from ..config import MCPConfig
from .mcp import refresh_mcp_runtime
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPRefreshTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "mcp_refresh"

    @property
    def description(self) -> str:
        return "Refresh MCP tool discovery from configured external MCP servers"

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

        if persist:
            await refresh_mcp_runtime(llm_client, context.tool_registry, mcp_config)
        else:
            from .mcp import setup_mcp_for_llm_client

            await setup_mcp_for_llm_client(llm_client, mcp_config)

        return ToolResult.ok(
            {
                "success": True,
                "tool_count": len(getattr(llm_client, "mcp_tools", None) or []),
            }
        )
