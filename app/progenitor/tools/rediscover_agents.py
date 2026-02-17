"""
Rediscover Agents - Scan the agents folder, analyze with LLM, and update registry.
"""

import logging
from pathlib import Path

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class RediscoverAgentsTool(BaseSystemTool):
    """Scan the agents folder, analyze with LLM, and update registry.json."""

    @property
    def name(self) -> str:
        return "rediscover_agents"

    @property
    def description(self) -> str:
        return "Scan the agents folder, analyze with LLM, and update registry.json"

    @property
    def keywords(self) -> list[str]:
        return [
            "rewire agents",
            "rewire the agents",
            "rediscover agents",
            "rescan agents",
            "refresh agents",
            "reload agents",
            "scan agents",
            "discover agents",
            "update registry",
            "refresh registry",
            "sync agents",
            "re-scan agents",
            "re-discover agents",
            "agent discovery",
            "run agent discovery",
            "run discovery",
            "do agent discovery",
            "agent registry",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from .agent_discovery import discover_agents

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent
        agents_dir = project_root / "app" / "agents"

        logger.info("Executing system tool: rediscover_agents")

        result = await discover_agents(
            agents_dir=agents_dir,
            llm_client=context.llm_client,
            registry=context.tool_registry,
        )

        return ToolResult.ok(result)
