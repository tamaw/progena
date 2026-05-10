"""
MCP Refresh - Refresh live MCP tool discovery from data/config/mcp.json.
"""

import logging

from pydantic import BaseModel, Field

from ..config import MCPConfig
from ..mcp import attach_mcp_runtime_context, reload_mcp_runtime_context
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPRefreshArgs(BaseModel):
    """Structured arguments for MCP registry refresh."""

    persist: bool = Field(
        default=True,
        description="Persist refreshed MCP discovery into the runtime registry.",
    )


class MCPRefreshTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return MCPRefreshArgs

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
            await reload_mcp_runtime_context(
                llm_client,
                context.tool_registry,
                mcp_config,
                reload_config=True,
            )
        else:
            refreshed_config = MCPConfig.load(config_path=mcp_config.config_path)
            await attach_mcp_runtime_context(llm_client, refreshed_config)

        mcp_runtime = llm_client.mcp_runtime

        return ToolResult.ok(
            {
                "success": True,
                "tool_count": len(mcp_runtime.mcp_tools if mcp_runtime is not None else []),
            }
        )
