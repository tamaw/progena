"""
MCP Refresh - Refresh live MCP tool discovery from the shared MCP state document.
"""

import logging

from ..config import MCPConfig
from ..mcp import reload_mcp_runtime_context
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
        if not isinstance(mcp_config, MCPConfig):
            return ToolResult.fail("mcp_config unavailable")

        await reload_mcp_runtime_context(
            llm_client,
            context.tool_registry,
            mcp_config,
            state_manager=context.state_manager,
            reload_config=True,
        )

        mcp_runtime = llm_client.mcp_runtime

        return ToolResult.ok(
            {
                "success": True,
                "tool_count": len(mcp_runtime.mcp_tools if mcp_runtime is not None else []),
            }
        )
