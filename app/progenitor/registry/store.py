"""File-backed registry store and lookup helpers."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, TypedDict

from ..state.models import AgentRegistryEntry, AgentStatus
from ..tools.protocol import BaseSystemTool, ToolContext, ToolResult
from ..utils import utcnow

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


class RegistryData(TypedDict):
    """Type definition for registry data structure."""

    agents: list[dict[str, Any]]
    tools: list[dict[str, Any]]


class ToolRegistry:
    """Registry for A2A agents and MCP tools."""

    def __init__(self, registry_path: Path | str) -> None:
        self.registry_path: Path = Path(registry_path)
        self._registry: RegistryData = {"agents": [], "tools": []}

    async def load(self) -> None:
        """Load registry from disk."""
        runtime_tools = list(self._registry.get("tools", []))
        if self.registry_path.exists():
            try:
                with open(self.registry_path) as handle:
                    loaded = json.load(handle)
                agents: list[dict[str, Any]] = []
                if isinstance(loaded, dict):
                    raw_agents = loaded.get("agents")
                    if isinstance(raw_agents, list):
                        agents = [agent for agent in raw_agents if isinstance(agent, dict)]
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
                    "tools": runtime_tools,
                }
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("Failed to load registry: %s. Starting fresh.", exc)
                self._registry = {"agents": [], "tools": runtime_tools}
        else:
            logger.info("No existing registry found, creating new one")
            self._registry = {"agents": [], "tools": runtime_tools}

        logger.info(
            "Loaded registry with %d agents and %d runtime tools",
            len(self._registry.get("agents", [])),
            len(self._registry.get("tools", [])),
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
                key: value for key, value in agent.items() if key not in {"name", "agent_card"}
            }
        with open(self.registry_path, "w") as handle:
            json.dump(agent_map, handle, indent=2, default=str)
        logger.info("Saved registry to %s", self.registry_path)

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
        """Discover local FastMCP tools under app/mcp/."""
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
            description = (
                f"Local FastMCP tool (HTTP). Start with: python app/mcp/{entry.name}/server.py"
            )
            if spec_path.exists():
                try:
                    with open(spec_path, encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            description = line.lstrip("#").strip() if line.startswith("#") else line
                            break
                except OSError:
                    pass

            local_tools.append(
                {
                    "name": entry.name,
                    "description": description,
                    "type": "mcp_tool",
                    "source": "local",
                }
            )

        return local_tools

    def get_tool_context(self) -> str:
        """Get formatted tool and agent descriptions for the LLM context window."""
        sections: list[str] = []

        from ..tools.system_registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]
        lines = ["### System Tools"]
        for tool in system_tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "No description")
            lines.append(f"- {name}: {desc}")
        sections.append("\n".join(lines))

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
        for agent in self._registry.get("agents", []):
            if capability in agent.get("capabilities", []):
                return agent
        return None

    def find_tool_by_capability(self, capability: str) -> dict[str, Any] | None:
        for tool in self._registry.get("tools", []):
            if capability in tool.get("capabilities", []):
                return tool
        return None

    def find_by_name(self, name: str) -> dict[str, Any] | None:
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
        self._registry["agents"] = [
            agent for agent in self._registry.get("agents", []) if agent.get("name") != entry.name
        ]
        self._registry["agents"].append(entry.model_dump())
        await self.save()

    async def register_tool(self, entry: dict[str, Any]) -> None:
        self._registry["tools"] = [
            tool
            for tool in self._registry.get("tools", [])
            if tool.get("name") != entry.get("name")
        ]
        entry["updated_at"] = utcnow().isoformat()
        self._registry["tools"].append(entry)

    def set_runtime_tools(self, entries: list[dict[str, Any]]) -> None:
        deduped: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        updated_at = utcnow().isoformat()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            deduped.append({**entry, "updated_at": updated_at})
        self._registry["tools"] = deduped

    async def unregister(self, name: str) -> bool:
        original_agents = len(self._registry.get("agents", []))
        original_tools = len(self._registry.get("tools", []))

        self._registry["agents"] = [
            agent for agent in self._registry.get("agents", []) if agent.get("name") != name
        ]
        self._registry["tools"] = [
            tool for tool in self._registry.get("tools", []) if tool.get("name") != name
        ]

        removed = (
            len(self._registry.get("agents", [])) < original_agents
            or len(self._registry.get("tools", [])) < original_tools
        )
        if len(self._registry.get("agents", [])) < original_agents:
            await self.save()
        return removed

    def get_agents(self) -> list[dict[str, Any]]:
        return self._registry.get("agents", [])

    def get_tools(self) -> list[dict[str, Any]]:
        return self._registry.get("tools", [])

    def capability_exists(self, capability: str) -> bool:
        return (
            self.find_agent_by_capability(capability) is not None
            or self.find_tool_by_capability(capability) is not None
        )

    def get_running_agents(self) -> list[dict[str, Any]]:
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
        agent = self.find_by_name(name)
        if agent:
            url = agent.get("url")
            return url if isinstance(url, str) else None
        return None


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
        capabilities = (
            registry.get_all_capabilities() if hasattr(registry, "get_all_capabilities") else []
        )

        return ToolResult.ok(
            {
                "registry_path": str(getattr(registry, "registry_path", None)),
                "agent_count": len(agents),
                "tool_count": len(tools),
                "capability_count": len(capabilities),
                "capabilities": sorted(capabilities),
            }
        )
