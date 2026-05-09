"""
List Agents - List all registered agents with their status.
"""

import logging
from contextlib import suppress

from ..nodes.registry import refresh_agent_metadata
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
        # External deploy pipelines can update agents.json; reload best-effort.
        if hasattr(context.tool_registry, "load"):
            with suppress(Exception):
                await context.tool_registry.load()

        # Best-effort: keep agent metadata aligned to live agent cards and health.
        with suppress(Exception):
            await refresh_agent_metadata(context.tool_registry)

        agents = context.tool_registry.get_agents()

        def _capabilities(agent: dict) -> list[str]:
            card = agent.get("agent_card")
            if isinstance(card, dict):
                card_caps = card.get("capabilities")
                if isinstance(card_caps, list):
                    return [str(c) for c in card_caps if isinstance(c, str) and c]
                if isinstance(card.get("skills"), list):
                    caps: list[str] = []
                    for skill in card.get("skills", []):
                        if not isinstance(skill, dict):
                            continue
                        skill_id = str(skill.get("id") or "").strip()
                        skill_name = str(skill.get("name") or "").strip()
                        if skill_id:
                            caps.append(skill_id)
                        elif skill_name:
                            caps.append(skill_name)
                    if caps:
                        return caps
            existing = agent.get("capabilities")
            return [str(c) for c in existing] if isinstance(existing, list) else []

        all_agents = [
            {
                "name": agent.get("name"),
                "description": (
                    agent.get("agent_card", {}).get("description")
                    if isinstance(agent.get("agent_card"), dict)
                    else None
                )
                or agent.get("description"),
                "version": (
                    agent.get("agent_card", {}).get("version")
                    if isinstance(agent.get("agent_card"), dict)
                    else None
                )
                or agent.get("version"),
                "capabilities": _capabilities(agent),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
                "url": agent.get("url"),
                "agent_card_url": (
                    agent.get("agent_card", {}).get("url")
                    if isinstance(agent.get("agent_card"), dict)
                    else None
                ),
            }
            for agent in agents
        ]

        running = [a for a in agents if a.get("status") == "running"]

        return ToolResult.ok(
            {
                "agents": all_agents,
                "running_count": len(running),
                "total_count": len(all_agents),
            }
        )
