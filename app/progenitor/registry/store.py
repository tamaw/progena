"""Document-store backed registry for A2A agents and MCP tools."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypedDict

from ..state.models import AgentRegistryEntry, AgentStatus
from ..tools.protocol import BaseSystemTool, ToolContext, ToolResult
from ..utils import utcnow

# Document-store registry read/write callbacks.
RegistryLoader = Callable[[], Awaitable[dict[str, Any]]]
RegistrySaver = Callable[[dict[str, Any]], Awaitable[None]]

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

    def __init__(self) -> None:
        self._registry: RegistryData = {"agents": [], "tools": []}
        self._loader: RegistryLoader | None = None
        self._saver: RegistrySaver | None = None

    def bind_store(self, loader: RegistryLoader, saver: RegistrySaver) -> None:
        """Back the registry with a document store (DB) instead of the JSON file.

        When bound, :meth:`load`/:meth:`save` read/write the canonical registry
        document; the JSON file is only consulted once to migrate pre-existing
        entries into the store. Unbound, the registry uses the file directly.
        """
        self._loader = loader
        self._saver = saver

    @property
    def store_backed(self) -> bool:
        return self._saver is not None

    @staticmethod
    def _parse_agents(loaded: Any) -> list[dict[str, Any]]:
        """Normalize a registry document/file into a list of agent dicts.

        Accepts both the ``{"agents": [...]}`` shape and the legacy top-level
        ``{name: cfg}`` map shape.
        """
        agents: list[dict[str, Any]] = []
        if not isinstance(loaded, dict):
            return agents
        raw_agents = loaded.get("agents")
        if isinstance(raw_agents, list):
            agents = [agent for agent in raw_agents if isinstance(agent, dict)]
        agent_map = {
            str(name): cfg
            for name, cfg in loaded.items()
            if name not in {"agents", "tools"} and isinstance(cfg, dict)
        }
        if agent_map:
            seen = set(agent_map)
            agents = [a for a in agents if str(a.get("name") or "") not in seen]
            for name, cfg in agent_map.items():
                agents.append({"name": name, **cfg})
        return agents

    def _serialize(self) -> dict[str, Any]:
        """Registry document shape: agents (named) + runtime tools."""
        agents: list[dict[str, Any]] = []
        for agent in self._registry.get("agents", []):
            if isinstance(agent, dict) and str(agent.get("name") or "").strip():
                agents.append(agent)
        return {"agents": agents, "tools": list(self._registry.get("tools", []))}

    async def load(self) -> None:
        """Load the registry from the bound document store."""
        runtime_tools = list(self._registry.get("tools", []))

        if self._loader is not None:
            try:
                document = await self._loader()
            except Exception as exc:  # pragma: no cover - store dependent
                logger.error("Failed to load registry from store: %s. Starting fresh.", exc)
                document = {}
            agents = self._parse_agents(document)
            doc_tools = document.get("tools") if isinstance(document, dict) else None
            tools = doc_tools if isinstance(doc_tools, list) and doc_tools else runtime_tools
            self._registry = {"agents": agents, "tools": tools}
        else:
            self._registry = {"agents": [], "tools": runtime_tools}

        logger.info(
            "Loaded registry with %d agents and %d runtime tools (store-backed=%s)",
            len(self._registry.get("agents", [])),
            len(self._registry.get("tools", [])),
            self.store_backed,
        )

    async def save(self) -> None:
        """Persist the registry to the bound document store."""
        if self._saver is not None:
            await self._saver(self._serialize())
            logger.debug("Saved registry to store (%d agents)", len(self._registry["agents"]))
            return
        logger.debug("Registry has no saver bound; skipping persistence")

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
        # JSON mode so datetimes are ISO strings — the store-backed save serializes
        # to JSON without a default encoder (the file path used default=str).
        self._registry["agents"] = [
            agent for agent in self._registry.get("agents", []) if agent.get("name") != entry.name
        ]
        self._registry["agents"].append(entry.model_dump(mode="json"))
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

    async def execute(self, context: ToolContext) -> ToolResult:
        registry = context.tool_registry
        agents = registry.get_agents() if hasattr(registry, "get_agents") else []
        tools = registry.get_tools() if hasattr(registry, "get_tools") else []
        capabilities = (
            registry.get_all_capabilities() if hasattr(registry, "get_all_capabilities") else []
        )

        return ToolResult.ok(
            {
                "store_backed": getattr(registry, "store_backed", False),
                "agent_count": len(agents),
                "tool_count": len(tools),
                "capability_count": len(capabilities),
                "capabilities": sorted(capabilities),
            }
        )
