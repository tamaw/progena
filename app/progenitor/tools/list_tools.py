"""
List Tools - Lists all available tools including system tools, agents, and MCP tools.
"""

import logging

from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ListToolsTool(BaseSystemTool):
    """List all available tools including system tools, agents, and MCP tools."""

    @property
    def name(self) -> str:
        return "list_tools"

    @property
    def description(self) -> str:
        return "List all available tools and their capabilities"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ..nodes.node_helpers.registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]

        # Get registered MCP tools from registry
        mcp_tools = context.tool_registry.get_tools()
        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in mcp_tools
        ]

        return ToolResult.ok({
            "system_tools": system_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(mcp_tool_entries),
        })
