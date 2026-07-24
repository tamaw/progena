"""
List Tools - Lists all available tools including system tools, agents, and MCP tools.
"""

import logging
from contextlib import suppress
from typing import Any

from ..nodes.registry import refresh_agent_metadata
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

    async def execute(self, context: ToolContext) -> ToolResult:
        from .system_registry import get_all_system_tools

        # Best-effort refresh of agents.json and agent cards, which can change
        # during process uptime. MCP tool discovery is owned by the heartbeat,
        # the /config/mcp.json write-hook, and the mcp_refresh tool — listing
        # reads the already-synced registry rather than rebuilding the runtime.
        if hasattr(context.tool_registry, "load"):
            with suppress(Exception):
                await context.tool_registry.load()
        with suppress(Exception):
            await refresh_agent_metadata(context.tool_registry)

        system_tools: list[dict[str, Any]] = [tool.to_dict() for tool in get_all_system_tools()]

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
                "url": agent.get("url"),
            }
            for agent in agents
            if isinstance(agent, dict) and agent.get("name")
        ]

        # Get registered MCP tools from registry
        mcp_tools = (
            context.tool_registry.get_tools() if hasattr(context.tool_registry, "get_tools") else []
        )
        local_mcp_tools = []
        if hasattr(context.tool_registry, "get_local_mcp_tools"):
            local_mcp_tools = context.tool_registry.get_local_mcp_tools()

        # Include in-memory discovered MCP tools even if they haven't been
        # persisted to agents.json yet (e.g. in-process service path).
        discovered_mcp_tools: list[dict] = []
        mcp_runtime = getattr(getattr(context, "llm_client", None), "mcp_runtime", None)
        if mcp_runtime is not None:
            try:
                discovered_mcp_tools = mcp_runtime.get_tool_registry_entries() or []
            except Exception:
                discovered_mcp_tools = []

        combined_mcp_tools = list(mcp_tools or [])
        combined_mcp_tools.extend(local_mcp_tools or [])
        combined_mcp_tools.extend(discovered_mcp_tools or [])

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

        return ToolResult.ok(
            {
                "system_tools": system_tools,
                "agents": agent_entries,
                "mcp_tools": mcp_tool_entries,
                "total_count": len(system_tools) + len(agent_entries) + len(mcp_tool_entries),
                "summary": {
                    "system_tools": len(system_tools),
                    "agents": len(agent_entries),
                    "mcp_tools": len(mcp_tool_entries),
                },
            }
        )
