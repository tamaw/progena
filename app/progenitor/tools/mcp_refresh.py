"""
MCP Refresh - Refresh live MCP tool discovery from data/config/mcp.json.
"""

import logging
from typing import Any

from ..config import MCPConfig
from .mcp import refresh_mcp_runtime
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPRefreshTool(BaseSystemTool):
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "persist": {
                    "type": "boolean",
                    "description": "Persist refreshed MCP discovery into the runtime registry.",
                    "default": True,
                },
            },
            "required": [],
        }

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
        if not isinstance(mcp_config, MCPConfig):
            return ToolResult.fail("mcp_config unavailable")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        persist = bool(args.get("persist", True))

        if persist:
            await refresh_mcp_runtime(
                llm_client,
                context.tool_registry,
                mcp_config,
                reload_config=True,
            )
        else:
            refreshed_config = MCPConfig.load(config_path=mcp_config.config_path)
            from .mcp import setup_mcp_for_llm_client

            await setup_mcp_for_llm_client(llm_client, refreshed_config)

        return ToolResult.ok(
            {
                "success": True,
                "tool_count": len(getattr(llm_client, "mcp_tools", None) or []),
            }
        )
