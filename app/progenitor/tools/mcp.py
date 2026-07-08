"""MCP status tool: surface integration state and the discovered tool list.

Tool *invocation* is handled by ``MCPRuntime`` (``app/progenitor/mcp/runtime.py``)
via the LLM client; discovery/refresh is owned by ``sync_mcp_runtime_context``
(``app/progenitor/nodes/mcp.py``). This module only reads that runtime state.
"""

import logging

from ..config import MCPConfig
from ..mcp.tool_schema import get_mcp_tool_description
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPTool(BaseSystemTool):
    """Expose MCP integration status and discovered tool list."""

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return "Show MCP integration status and list discovered MCP tools"

    async def execute(self, context: ToolContext) -> ToolResult:
        llm_client = context.llm_client
        mcp_config = getattr(llm_client, "mcp_config", None)
        mcp_runtime = getattr(llm_client, "mcp_runtime", None)
        mcp_tools = mcp_runtime.mcp_tools if mcp_runtime is not None else []
        runtime_servers = mcp_runtime.mcp_servers if mcp_runtime is not None else {}
        disabled: set[str] = (
            getattr(mcp_runtime, "disabled_tools", set()) if mcp_runtime is not None else set()
        )

        # Report the live (enabled) tool set — the tools actually exposed to the
        # agent — since per-tool toggles disable tools without removing them.
        live_tools = [
            tool
            for tool in mcp_tools
            if isinstance(tool, dict) and str(tool.get("name") or "") not in disabled
        ]

        configured_servers = []
        if isinstance(mcp_config, MCPConfig):
            configured_servers = [
                {
                    "name": server.name,
                    "url": server.url,
                    "transport": server.transport,
                    "enabled": server.enabled,
                }
                for server in mcp_config.enabled_servers
            ]

        return ToolResult.ok(
            {
                "enabled": bool(configured_servers),
                "server_count": len(configured_servers),
                "servers": configured_servers,
                "connected_server_count": len(runtime_servers),
                "tool_count": len(live_tools),
                "disabled_count": len(mcp_tools) - len(live_tools),
                "tools": [
                    {
                        "name": tool.get("name"),
                        "description": get_mcp_tool_description(tool),
                        "server": tool.get("mcp_server_name"),
                    }
                    for tool in live_tools
                ],
            }
        )
