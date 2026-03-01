"""
List Agents - List all registered agents with their status.
"""

import logging
from contextlib import suppress

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ListAgentsTool(BaseSystemTool):
    """List all registered agents with their status."""

    @property
    def name(self) -> str:
        return "list_agents"

    @property
    def description(self) -> str:
        return "List all registered agents with their status"

    @property
    def keywords(self) -> list[str]:
        return [
            "list agents",
            "show agents",
            "what agents",
            "which agents",
            "available agents",
            "running agents",
            "active agents",
            "agent status",
            "agent list",
            "discover agents",
            "rediscover agents",
            "refresh agents",
            "reload agents",
            "rescan agents",
            "scan agents",
            "rewire agents",
            "rewire the agents",
            "refresh registry",
            "re-scan agents",
            "re-discover agents",
            "agent registry",
            "run discovery",
            "sync agents",
            "agent discovery",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        # External deploy pipelines can update registry.json; reload best-effort.
        if hasattr(context.tool_registry, "load"):
            with suppress(Exception):
                await context.tool_registry.load()

        # Keep local folder-based discovery available via this single tool path.
        with suppress(Exception):
            from pathlib import Path

            from .agent_discovery import discover_agents

            project_root = Path(__file__).parent.parent.parent.parent
            agents_dir = project_root / "app" / "agents"
            if agents_dir.exists():
                await discover_agents(
                    agents_dir=agents_dir,
                    llm_client=context.llm_client,
                    registry=context.tool_registry,
                )

        # Best-effort: keep agent metadata aligned to live agent cards.
        if hasattr(context.tool_registry, "refresh_agent_cards"):
            with suppress(Exception):
                from .mcp import A2AClient

                a2a_client = A2AClient()
                await context.tool_registry.refresh_agent_cards(a2a_client)

        agents = context.tool_registry.get_agents()
        all_agents = [
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
        ]

        running = [a for a in agents if a.get("status") == "running"]

        return ToolResult.ok({
            "agents": all_agents,
            "running_count": len(running),
            "total_count": len(all_agents),
        })
