"""
Tool and Agent Registry for Progenitor.

Maintains agents.json with agent manifests and mcp.json with MCP tools.
Provides capability discovery and lookup for routing decisions.
Tracks runtime status of HTTP-based A2A agents.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, TypedDict

from ..runtime.port_utils import find_available_port
from ..runtime.time_utils import utcnow
from ..state.models import AgentRegistryEntry, AgentStatus
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_TOOL_NAME_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")


def normalize_tool_name(name: str) -> str:
    """Normalize an arbitrary display name into a valid OpenAI function/tool name."""
    raw = (name or "").strip()
    if not raw:
        return "tool"
    slug = _TOOL_NAME_INVALID_CHARS.sub("_", raw).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return (slug or "tool")[:64]


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
        self.mcp_path: Path = self.registry_path.with_name("mcp.json")
        self.host = host  # Host for agent endpoints
        self._registry: RegistryData = {"agents": [], "tools": []}

    async def load(self) -> None:
        """Load registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path) as f:
                    loaded = json.load(f)
                agents: list[dict[str, Any]] = []
                if isinstance(loaded, dict):
                    raw_agents = loaded.get("agents")
                    if isinstance(raw_agents, list):
                        agents = [a for a in raw_agents if isinstance(a, dict)]
                    agent_map = {
                        str(name): cfg
                        for name, cfg in loaded.items()
                        if name != "agents" and isinstance(cfg, dict)
                    }
                    if agent_map:
                        filtered = []
                        seen_names = set(agent_map)
                        for agent in agents:
                            name = str(agent.get("name") or "")
                            if name and name in seen_names:
                                continue
                            filtered.append(agent)
                        agents = filtered
                        for name, cfg in agent_map.items():
                            agents.append({"name": name, **cfg})
                self._registry = {
                    "agents": agents if isinstance(agents, list) else [],
                    "tools": [],
                }
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load registry: %s. Starting fresh.", e)
                self._registry = {"agents": [], "tools": []}
        else:
            logger.info("No existing registry found, creating new one")

        if self.mcp_path.exists():
            try:
                with open(self.mcp_path) as f:
                    mcp_data = json.load(f)
                tools = mcp_data.get("tools", []) if isinstance(mcp_data, dict) else []
                self._registry["tools"] = tools if isinstance(tools, list) else []
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load MCP state: %s. Starting with no MCP tools.", e)
                self._registry["tools"] = []
        else:
            self._registry["tools"] = []

        logger.info(
            "Loaded registry with %d agents and %d tools",
            len(self._registry.get('agents', [])),
            len(self._registry.get('tools', [])),
        )

    async def save(self) -> None:
        """Save registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        agent_map: dict[str, dict[str, Any]] = {}
        for agent in self._registry.get("agents", []):
            if not isinstance(agent, dict):
                continue
            name = str(agent.get("name") or "").strip()
            if not name:
                continue
            agent_map[name] = {
                k: v
                for k, v in agent.items()
                if k not in {"name", "agent_card"}
            }
        with open(self.registry_path, 'w') as f:
            json.dump(agent_map, f, indent=2, default=str)
        with open(self.mcp_path, 'w') as f:
            json.dump({"tools": self._registry.get("tools", [])}, f, indent=2, default=str)
        logger.info("Saved registry to %s and MCP state to %s", self.registry_path, self.mcp_path)

    async def clear_agents(self) -> None:
        """No-op: agent registry entries are append/update-only."""
        logger.warning("clear_agents() is disabled; registry entries are update-only")

    def get_all_capabilities(self) -> list[str]:
        """Get all registered capabilities (from agents and tools)."""
        capabilities = []

        for agent in self._registry.get("agents", []):
            capabilities.extend(agent.get("capabilities", []))

        for tool in self._registry.get("tools", []):
            capabilities.extend(tool.get("capabilities", []))

        return list(set(capabilities))

    def get_local_mcp_tools(self) -> list[dict[str, Any]]:
        """Discover local FastMCP tools under app/mcp/.

        These are HTTP MCP tools that may not be registered in agents/mcp state.
        """
        tools_dir = Path(__file__).resolve().parents[3] / "app" / "mcp"
        if not tools_dir.exists():
            return []

        local_tools: list[dict[str, Any]] = []
        for entry in tools_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name == "__pycache__":
                continue

            server_path = entry / "server.py"
            if not server_path.exists():
                continue

            spec_path = entry / "spec.md"
            description = f"Local FastMCP tool (HTTP). Start with: python app/mcp/{entry.name}/server.py"

            if spec_path.exists():
                try:
                    with open(spec_path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            description = line.lstrip("#").strip() if line.startswith("#") else line
                            break
                except OSError:
                    pass

            local_tools.append({
                "name": entry.name,
                "description": description,
                "type": "mcp_tool",
                "source": "local",
            })

        return local_tools

    def get_tool_context(self) -> str:
        """Get formatted tool and agent descriptions for the LLM context window.

        Returns a structured string describing all available system tools, A2A
        agents, and MCP tools so the LLM can make informed routing decisions.
        """
        sections: list[str] = []

        # System tools (always available)
        from ..nodes.node_helpers.registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]
        lines = ["### System Tools"]
        for tool in system_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "No description")
            lines.append(f"- {name}: {desc}")

        # Build actions are handled via decide -> BackgroundBuildRunner, but they are
        # still important routing capabilities to surface in tool context.
        lines.append("- build_agent: Build a new A2A agent in the background (explicit requests only)")
        lines.append("- build_mcp: Build a new FastMCP tool/server in the background (explicit requests only)")
        lines.append("- build_progenitor: Improve Progenitor in the background (explicit requests only)")
        sections.append("\n".join(lines))

        # A2A agents from registry
        agents = self._registry.get("agents", [])
        if agents:
            lines = ["### A2A Agents"]
            for agent in agents:
                name = agent.get("name", "unknown")
                desc = agent.get("description", "No description")
                status = agent.get("status", "unknown")
                caps = agent.get("capabilities", [])
                lines.append(f"- {name} [{status}]: {desc}")
                if caps:
                    lines.append(f"  Capabilities: {', '.join(caps)}")
            sections.append("\n".join(lines))

        # MCP tools from registry + local FastMCP tools from app/mcp
        tools = self._registry.get("tools", [])
        local_tools = self.get_local_mcp_tools()
        seen_names = {tool.get("name") for tool in tools}
        for local_tool in local_tools:
            if local_tool.get("name") not in seen_names:
                tools.append(local_tool)

        if tools:
            lines = ["### MCP Tools"]
            for tool in tools:
                name = tool.get("name", "unknown")
                desc = tool.get("description", "No description")
                caps = tool.get("capabilities", [])
                lines.append(f"- {name}: {desc}")
                if caps:
                    lines.append(f"  Capabilities: {', '.join(caps)}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def find_agent_by_capability(self, capability: str) -> dict[str, Any] | None:
        """Find an agent that provides a capability."""
        for agent in self._registry.get("agents", []):
            if capability in agent.get("capabilities", []):
                return agent
        return None

    def find_tool_by_capability(self, capability: str) -> dict[str, Any] | None:
        """Find an MCP tool that provides a capability."""
        for tool in self._registry.get("tools", []):
            if capability in tool.get("capabilities", []):
                return tool
        return None

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """Find an agent or tool by name."""
        needle = normalize_tool_name(name)
        for agent in self._registry.get("agents", []):
            agent_name = str(agent.get("name") or "")
            if agent_name == name or normalize_tool_name(agent_name) == needle:
                return agent

        for tool in self._registry.get("tools", []):
            tool_name = str(tool.get("name") or "")
            if tool_name == name or normalize_tool_name(tool_name) == needle:
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
            self.find_agent_by_capability(capability) is not None
            or self.find_tool_by_capability(capability) is not None
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
        claimed_ports: set[int] = set()
        for agent in self._registry.get("agents", []):
            port = agent.get("port")
            if isinstance(port, int):
                claimed_ports.add(port)

        try:
            return find_available_port(
                start_port=AGENT_PORT_RANGE_START,
                end_port=AGENT_PORT_RANGE_END,
                host=self.host,
                exclude_ports=claimed_ports,
            )
        except RuntimeError as e:
            raise RuntimeError(
                f"No available ports in range {AGENT_PORT_RANGE_START}-{AGENT_PORT_RANGE_END}"
            ) from e

    async def update_agent_status(
        self,
        name: str,
        status: AgentStatus,
        *,
        status_message: str | None = None,
        pid: int | None = None,
        port: int | None = None,
        last_error: str | None = None,
        update_heartbeat: bool = False,
    ) -> bool:
        """Update runtime status of an agent."""
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
                    agent["url"] = f"http://{self.host}:{port}"
                    agent["agent_card_url"] = f"{agent['url']}/.well-known/agent-card.json"

                if last_error is not None:
                    agent["last_error"] = last_error
                elif status == AgentStatus.RUNNING:
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
                    f" - {status_message}" if status_message else "",
                )
                return True

        logger.warning("Agent '%s' not found for status update", name)
        return False

    async def mark_agent_starting(self, name: str, pid: int, port: int) -> bool:
        """Mark an agent as starting up."""
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
            agent
            for agent in self._registry.get("agents", [])
            if agent.get("status")
            in (
                AgentStatus.RUNNING.value,
                AgentStatus.WAITING.value,
                AgentStatus.EXECUTING.value,
            )
        ]

    def get_agent_endpoint(self, name: str) -> str | None:
        """Get the HTTP URL for an agent."""
        agent = self.find_by_name(name)
        if agent:
            return agent.get("url")
        return None

    async def refresh_agent_cards(
        self,
        a2a_client: Any,
        *,
        timeout: float = 2.0,
        concurrency: int = 8,
        persist: bool = False,
    ) -> dict[str, Any]:
        """Best-effort refresh agent cards in memory."""
        agents = self._registry.get("agents", [])
        if not agents:
            return {"agents_checked": 0, "agents_updated": 0, "errors": []}

        sem = asyncio.Semaphore(max(1, int(concurrency)))
        updated = 0
        errors: list[str] = []
        changed_any = False

        async def _refresh_one(agent: dict[str, Any]) -> None:
            nonlocal updated, changed_any
            name = str(agent.get("name") or "")
            if not name:
                return
            if not agent.get("url") and not agent.get("agent_card_url"):
                return

            async with sem:
                try:
                    card = await a2a_client.get_agent_card(agent, timeout=timeout)
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
                    return

            if not isinstance(card, dict):
                return

            # Keep a copy for debugging/UI without depending on it elsewhere.
            agent["agent_card"] = card
            updated += 1
            changed_any = True

        await asyncio.gather(*[_refresh_one(a) for a in agents if isinstance(a, dict)])

        if changed_any and persist:
            await self.save()

        return {"agents_checked": len(agents), "agents_updated": updated, "errors": errors}

    async def refresh_agent_health(
        self,
        *,
        timeout: float = 2.0,
        concurrency: int = 8,
        persist: bool = False,
    ) -> dict[str, Any]:
        """Best-effort refresh of /health status for configured agents."""
        import aiohttp

        agents = self._registry.get("agents", [])
        if not agents:
            return {"agents_checked": 0, "healthy": 0, "unhealthy": 0, "errors": []}

        sem = asyncio.Semaphore(max(1, int(concurrency)))
        healthy = 0
        unhealthy = 0
        errors: list[str] = []
        changed_any = False

        async def _check_one(agent: dict[str, Any]) -> None:
            nonlocal healthy, unhealthy, changed_any
            name = str(agent.get("name") or "")
            url = str(agent.get("url") or "").strip().rstrip("/")
            if not name or not url:
                return
            health_url = f"{url}/health"

            async with sem:
                try:
                    async with aiohttp.ClientSession() as session, session.get(
                        health_url,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        if response.status < 400:
                            healthy += 1
                            if agent.get("status") != AgentStatus.RUNNING.value:
                                changed_any = True
                            agent["status"] = AgentStatus.RUNNING.value
                            agent["status_message"] = "Health check passed"
                            return
                        raise RuntimeError(f"HTTP {response.status}")
                except Exception as exc:
                    unhealthy += 1
                    errors.append(f"{name}: {exc}")
                    if agent.get("status") != AgentStatus.FAILED.value:
                        changed_any = True
                    agent["status"] = AgentStatus.FAILED.value
                    agent["status_message"] = f"Health check failed: {exc}"

        await asyncio.gather(*[_check_one(a) for a in agents if isinstance(a, dict)])

        if changed_any and persist:
            await self.save()

        return {
            "agents_checked": len(agents),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "errors": errors,
        }

class RegistryTool(BaseSystemTool):
    """Expose the current agent/tool registry summary."""

    @property
    def name(self) -> str:
        return "registry"

    @property
    def description(self) -> str:
        return "Show agents.json summary (agents, tools, capabilities)"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        registry = context.tool_registry
        agents = registry.get_agents() if hasattr(registry, "get_agents") else []
        tools = registry.get_tools() if hasattr(registry, "get_tools") else []
        capabilities = registry.get_all_capabilities() if hasattr(registry, "get_all_capabilities") else []

        return ToolResult.ok({
            "registry_path": str(getattr(registry, "registry_path", None)),
            "agent_count": len(agents),
            "tool_count": len(tools),
            "capability_count": len(capabilities),
            "capabilities": sorted(capabilities),
        })
