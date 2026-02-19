"""
List Tools - Lists all available tools including system tools, agents, and MCP tools.
"""

import logging
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult

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

        system_tools: list[dict[str, Any]] = [tool.to_dict() for tool in get_all_system_tools()]

        # BUILD actions are first-class capabilities (they start background jobs),
        # even though they are routed as decision actions rather than executed as system tools.
        build_actions = [
            {
                "name": "build_agent",
                "description": "Build a new A2A agent in the background (requires explicit user request)",
                "type": "build_action",
            },
            {
                "name": "build_tool",
                "description": "Build a new FastMCP tool in the background (requires explicit user request)",
                "type": "build_action",
            },
            {
                "name": "upgrade",
                "description": "Upgrade an existing agent/tool in the background (requires explicit user request)",
                "type": "build_action",
            },
        ]
        system_tools = system_tools + build_actions

        # Include IPC user-communication system tools that aren't implemented as BaseSystemTool.
        from .user_communication import USER_COMMUNICATION_TOOLS

        system_tools = system_tools + list(USER_COMMUNICATION_TOOLS)

        # Get registered A2A agents from registry
        agents: list[dict[str, Any]] = []
        if hasattr(context.tool_registry, "get_agents"):
            agents = context.tool_registry.get_agents() or []

        agent_entries = [
            {
                "name": agent.get("name"),
                "description": agent.get("description", ""),
                "type": "a2a_agent",
                "capabilities": agent.get("capabilities", []),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
            if isinstance(agent, dict) and agent.get("name")
        ]

        # Get registered MCP tools from registry
        mcp_tools = context.tool_registry.get_tools() if hasattr(context.tool_registry, "get_tools") else []
        local_mcp_tools = []
        if hasattr(context.tool_registry, "get_local_mcp_tools"):
            local_mcp_tools = context.tool_registry.get_local_mcp_tools()

        # Include MCP tools discovered via LiteLLM even if they haven't been
        # persisted to registry.json yet (e.g. in-process service path).
        litellm_mcp_tools: list[dict] = []
        if getattr(context, "llm_client", None) is not None and hasattr(context.llm_client, "get_mcp_tool_registry_entries"):
            try:
                litellm_mcp_tools = context.llm_client.get_mcp_tool_registry_entries() or []
            except Exception:
                litellm_mcp_tools = []

        combined_mcp_tools = list(mcp_tools or [])
        combined_mcp_tools.extend(local_mcp_tools or [])
        combined_mcp_tools.extend(litellm_mcp_tools or [])

        # De-duplicate by name
        deduped_mcp_tools: list[dict] = []
        seen_names: set[str] = set()
        for tool in combined_mcp_tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            deduped_mcp_tools.append(tool)

        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in deduped_mcp_tools
        ]

        return ToolResult.ok({
            "system_tools": system_tools,
            "agents": agent_entries,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(agent_entries) + len(mcp_tool_entries),
            "summary": {
                "system_tools": len(system_tools),
                "agents": len(agent_entries),
                "mcp_tools": len(mcp_tool_entries),
            },
        })
