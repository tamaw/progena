"""
Tool and Agent Registry for Progenitor.

Maintains registry.json with agent manifests and MCP tools.
Provides capability discovery and lookup for routing decisions.
Tracks runtime status of HTTP-based A2A agents.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional, TypedDict

from ..state.models import AgentRegistryEntry, AgentStatus
from ..runtime.time_utils import utcnow
from ..runtime.port_utils import find_available_port, is_port_available

logger = logging.getLogger(__name__)


# Port range for A2A agents (8100-8199 by default)
AGENT_PORT_RANGE_START = 8100
AGENT_PORT_RANGE_END = 8199


class RegistryData(TypedDict):
    """Type definition for registry data structure."""
    agents: list[dict[str, Any]]
    tools: list[dict[str, Any]]


class ToolRegistry:
    """Registry for A2A agents and MCP tools.

    Manages agent lifecycle including:
    - Port allocation for HTTP-based agents
    - Runtime status tracking (running, failed, etc.)
    - Agent card URL management
    """

    def __init__(self, registry_path: Path | str, host: str = "localhost") -> None:
        self.registry_path: Path = Path(registry_path)
        self.host = host  # Host for agent endpoints
        self._registry: RegistryData = {"agents": [], "tools": []}

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
        logger.info("Saved registry to %s", self.registry_path)

    async def clear_agents(self) -> None:
        """Clear all registered agents from the registry."""
        self._registry["agents"] = []
        logger.info("Cleared all agents from registry")

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

    def allocate_port(self) -> int:
        """Allocate an available port for a new agent.

        Checks both the registry (ports claimed by agents) and the OS
        (ports actually bound) to find a truly available port.

        Returns:
            An unused port in the agent port range.

        Raises:
            RuntimeError: If no ports are available.
        """
        # Get ports already claimed in registry
        claimed_ports = set()
        for agent in self._registry.get("agents", []):
            port = agent.get("port")
            if port is not None:
                claimed_ports.add(port)

        # Use port_utils to find an actually available port
        try:
            return find_available_port(
                start_port=AGENT_PORT_RANGE_START,
                end_port=AGENT_PORT_RANGE_END,
                host=self.host,
                exclude_ports=claimed_ports,
            )
        except RuntimeError:
            raise RuntimeError(
                f"No available ports in range {AGENT_PORT_RANGE_START}-{AGENT_PORT_RANGE_END}"
            )

    async def update_agent_status(
        self,
        name: str,
        status: AgentStatus,
        *,
        status_message: Optional[str] = None,
        pid: Optional[int] = None,
        port: Optional[int] = None,
        last_error: Optional[str] = None,
        update_heartbeat: bool = False,
    ) -> bool:
        """Update runtime status of an agent.

        Called when agent process outputs status information or
        when health checks detect state changes.

        Args:
            name: Agent name.
            status: New status.
            status_message: Optional human-readable status detail.
            pid: Process ID (set when starting).
            port: Assigned port (set when starting).
            last_error: Error message if status is FAILED.
            update_heartbeat: Whether to update last_heartbeat timestamp.

        Returns:
            True if agent was found and updated.
        """
        for agent in self._registry.get("agents", []):
            if agent.get("name") == name:
                now = utcnow()
                agent["status"] = status.value if isinstance(status, AgentStatus) else status
                agent["updated_at"] = now.isoformat()

                if status_message is not None:
                    agent["status_message"] = status_message

                if pid is not None:
                    agent["pid"] = pid

                if port is not None:
                    agent["port"] = port
                    agent["endpoint"] = f"http://{self.host}:{port}"
                    agent["agent_card_url"] = f"http://{self.host}:{port}/.well-known/agent.json"

                if last_error is not None:
                    agent["last_error"] = last_error
                elif status == AgentStatus.RUNNING:
                    # Clear error on successful start
                    agent["last_error"] = None

                if status == AgentStatus.RUNNING and agent.get("startup_time") is None:
                    agent["startup_time"] = now.isoformat()

                if status == AgentStatus.STOPPED:
                    agent["pid"] = None
                    agent["startup_time"] = None

                if update_heartbeat:
                    agent["last_heartbeat"] = now.isoformat()

                await self.save()
                logger.info(
                    "Agent '%s' status updated: %s%s",
                    name,
                    status.value if isinstance(status, AgentStatus) else status,
                    f" - {status_message}" if status_message else ""
                )
                return True

        logger.warning("Agent '%s' not found for status update", name)
        return False

    async def mark_agent_starting(
        self,
        name: str,
        pid: int,
        port: int,
    ) -> bool:
        """Mark an agent as starting up.

        Args:
            name: Agent name.
            pid: Process ID.
            port: Assigned HTTP port.

        Returns:
            True if agent was found and updated.
        """
        return await self.update_agent_status(
            name,
            AgentStatus.STARTING,
            pid=pid,
            port=port,
            status_message="Process spawned, waiting for ready signal",
        )

    async def mark_agent_running(self, name: str) -> bool:
        """Mark an agent as running and healthy."""
        return await self.update_agent_status(
            name,
            AgentStatus.RUNNING,
            status_message="Accepting requests",
            update_heartbeat=True,
        )

    async def mark_agent_failed(self, name: str, error: str) -> bool:
        """Mark an agent as failed."""
        return await self.update_agent_status(
            name,
            AgentStatus.FAILED,
            status_message="Failed to start or crashed",
            last_error=error,
        )

    async def mark_agent_stopped(self, name: str) -> bool:
        """Mark an agent as stopped."""
        return await self.update_agent_status(
            name,
            AgentStatus.STOPPED,
            status_message="Process terminated",
        )

    def get_running_agents(self) -> list[dict[str, Any]]:
        """Get all agents that are currently running."""
        return [
            agent for agent in self._registry.get("agents", [])
            if agent.get("status") in (
                AgentStatus.RUNNING.value,
                AgentStatus.WAITING.value,
                AgentStatus.EXECUTING.value,
            )
        ]

    def get_agent_endpoint(self, name: str) -> Optional[str]:
        """Get the HTTP endpoint for an agent."""
        agent = self.find_by_name(name)
        if agent:
            return agent.get("endpoint")
        return None
