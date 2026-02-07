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

    @property
    def keywords(self) -> list[str]:
        return [
            "list tools",
            "show tools",
            "list your tools",
            "show your tools",
            "what tools",
            "which tools",
            "available tools",
            "list all tool",
            "show all tool",
            "list capabilit",
            "show capabilit",
            "what can you do",
            "your capabilit",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from ..nodes.node_helpers.registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]

        # Get registered MCP tools from registry
        mcp_tools = context.tool_registry.get_tools()
        local_mcp_tools = []
        if hasattr(context.tool_registry, "get_local_mcp_tools"):
            local_mcp_tools = context.tool_registry.get_local_mcp_tools()

        seen_names = {tool.get("name") for tool in mcp_tools}
        combined_mcp_tools = list(mcp_tools)
        for tool in local_mcp_tools:
            if tool.get("name") not in seen_names:
                combined_mcp_tools.append(tool)

        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in combined_mcp_tools
        ]

        return ToolResult.ok({
            "system_tools": system_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(mcp_tool_entries),
        })
