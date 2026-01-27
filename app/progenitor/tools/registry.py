"""
Tool and Agent Registry for Progenitor.

Maintains registry.json with agent manifests and MCP tools.
Provides capability discovery and lookup for routing decisions.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from ..state.models import AgentRegistryEntry
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for A2A agents and MCP tools."""

    def __init__(self, registry_path: Path):
        self.registry_path = Path(registry_path)
        self._registry: dict[str, Any] = {"agents": [], "tools": []}

    async def load(self) -> None:
        """Load registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, 'r') as f:
                    self._registry = json.load(f)
                logger.info(
                    "Loaded registry with %d agents and %d tools",
                    len(self._registry.get('agents', [])),
                    len(self._registry.get('tools', []))
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to load registry: %s. Starting fresh.", e)
                self._registry = {"agents": [], "tools": []}
        else:
            logger.info("No existing registry found, creating new one")
            await self.save()

    async def save(self) -> None:
        """Save registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, 'w') as f:
            json.dump(self._registry, f, indent=2, default=str)

    def get_all_capabilities(self) -> list[str]:
        """Get all registered capabilities (from agents and tools)."""
        capabilities = []

        for agent in self._registry.get("agents", []):
            capabilities.extend(agent.get("capabilities", []))

        for tool in self._registry.get("tools", []):
            capabilities.extend(tool.get("capabilities", []))

        return list(set(capabilities))

    def find_agent_by_capability(self, capability: str) -> Optional[dict[str, Any]]:
        """Find an agent that provides a capability."""
        for agent in self._registry.get("agents", []):
            if capability in agent.get("capabilities", []):
                return agent
        return None

    def find_tool_by_capability(self, capability: str) -> Optional[dict[str, Any]]:
        """Find an MCP tool that provides a capability."""
        for tool in self._registry.get("tools", []):
            if capability in tool.get("capabilities", []):
                return tool
        return None

    def find_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Find an agent or tool by name."""
        for agent in self._registry.get("agents", []):
            if agent.get("name") == name:
                return agent

        for tool in self._registry.get("tools", []):
            if tool.get("name") == name:
                return tool

        return None

    async def register_agent(self, entry: AgentRegistryEntry) -> None:
        """Register a new A2A agent."""
        # Remove existing with same name
        self._registry["agents"] = [
            a for a in self._registry.get("agents", [])
            if a.get("name") != entry.name
        ]

        self._registry["agents"].append(entry.model_dump())
        await self.save()

    async def register_tool(self, entry: dict[str, Any]) -> None:
        """Register a new MCP tool."""
        # Remove existing with same name
        self._registry["tools"] = [
            t for t in self._registry.get("tools", [])
            if t.get("name") != entry.get("name")
        ]

        entry["updated_at"] = utcnow().isoformat()
        self._registry["tools"].append(entry)
        await self.save()

    async def unregister(self, name: str) -> bool:
        """Unregister an agent or tool by name."""
        original_agents = len(self._registry.get("agents", []))
        original_tools = len(self._registry.get("tools", []))

        self._registry["agents"] = [
            a for a in self._registry.get("agents", [])
            if a.get("name") != name
        ]
        self._registry["tools"] = [
            t for t in self._registry.get("tools", [])
            if t.get("name") != name
        ]

        removed = (
            len(self._registry.get("agents", [])) < original_agents or
            len(self._registry.get("tools", [])) < original_tools
        )

        if removed:
            await self.save()

        return removed

    def get_agents(self) -> list[dict[str, Any]]:
        """Get all registered agents."""
        return self._registry.get("agents", [])

    def get_tools(self) -> list[dict[str, Any]]:
        """Get all registered tools."""
        return self._registry.get("tools", [])

    def capability_exists(self, capability: str) -> bool:
        """Check if a capability is registered."""
        return (
            self.find_agent_by_capability(capability) is not None or
            self.find_tool_by_capability(capability) is not None
        )
