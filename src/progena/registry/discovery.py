"""Agent discovery domain logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..instructions import render_runtime_instruction
from .refresh import refresh_agent_metadata

if TYPE_CHECKING:
    from src.progena.llm.client import LLMClient

    from .store import ToolRegistry

logger = logging.getLogger(__name__)

SKIP_FOLDERS = {"__pycache__", ".git", "base", ".venv", "node_modules"}
SKIP_FILES = {"__init__.py", ".gitkeep", ".DS_Store"}


def _read_manifest(path: Path) -> dict[str, Any]:
    """Parse an ``agent.yml`` manifest, returning {} on any error."""
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Failed to read agent manifest %s: %s", path, exc)
        return {}


class DiscoveredAgentSpec(BaseModel):
    """Structured output for an agent discovered from source files."""

    name: str = Field(description="The agent's name (derived from folder name or code)")
    description: str = Field(description="A brief description of what this agent does")
    capabilities: list[str] = Field(
        default_factory=list, description="List of capabilities this agent provides"
    )
    entrypoint: str = Field(description="The main Python file to run this agent")
    transport: str = Field(default="http", description="Transport protocol: 'http'")
    port: int | None = Field(
        default=None, description="Explicit service port declared by the agent manifest, if known"
    )
    url: str | None = Field(
        default=None, description="Explicit base URL for an already-running A2A agent"
    )
    agent_card_url: str | None = Field(
        default=None, description="Explicit agent-card URL for an already-running A2A agent"
    )
    version: str = Field(default="0.1.0", description="Agent version")
    is_valid_agent: bool = Field(
        default=True, description="Whether this folder contains a valid runnable agent"
    )
    reason_if_invalid: str | None = Field(
        default=None, description="If not a valid agent, explain why"
    )


class AgentDiscoveryResult(BaseModel):
    """Structured output for the full discovery process."""

    agents: list[DiscoveredAgentSpec] = Field(
        default_factory=list, description="List of discovered agents"
    )
    scan_timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="When the scan was performed",
    )


@dataclass
class AgentFolderInfo:
    """Information about an agent folder collected during scan."""

    folder_name: str
    folder_path: Path
    files: list[str] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def manifest_port(self) -> int | None:
        runtime = self.manifest.get("runtime") if isinstance(self.manifest, dict) else None
        if isinstance(runtime, dict):
            port = runtime.get("port")
            if isinstance(port, int):
                return port
            if isinstance(port, str) and port.strip().isdigit():
                return int(port.strip())
        return None

    def to_prompt_string(self) -> str:
        lines = [f"## Agent Folder: {self.folder_name}"]
        lines.append(f"Path: {self.folder_path}")
        lines.append(f"Files: {', '.join(self.files)}")
        lines.append("")
        for filename, content in self.file_contents.items():
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            lines.append(f"### {filename}")
            lines.append("BEGIN FILE CONTENT")
            lines.append(content)
            lines.append("END FILE CONTENT")
            lines.append("")
        return "\n".join(lines)


class AgentDiscovery:
    """Discovers agents from the agents/ folder and registers them."""

    def __init__(
        self,
        agents_dir: Path,
        llm_client: LLMClient,
        registry: ToolRegistry,
        host: str = "localhost",
        state_manager: Any = None,
    ):
        self.agents_dir = Path(agents_dir)
        self.llm_client = llm_client
        self.registry = registry
        self.host = host
        self.state_manager = state_manager

    def scan_agents_folder(self) -> list[AgentFolderInfo]:
        if not self.agents_dir.exists():
            logger.warning("Agents directory does not exist: %s", self.agents_dir)
            return []

        folders = []
        for item in self.agents_dir.iterdir():
            if not item.is_dir() or item.name in SKIP_FOLDERS:
                continue
            folder_info = AgentFolderInfo(folder_name=item.name, folder_path=item)
            for file_item in item.iterdir():
                if file_item.is_file() and file_item.name not in SKIP_FILES:
                    folder_info.files.append(file_item.name)
                    if file_item.suffix == ".py":
                        try:
                            folder_info.file_contents[file_item.name] = file_item.read_text(
                                encoding="utf-8"
                            )
                        except Exception as exc:
                            logger.warning("Failed to read %s: %s", file_item, exc)
                    elif file_item.name in {"agent.yml", "agent.yaml"}:
                        folder_info.manifest = _read_manifest(file_item)
            if folder_info.file_contents:
                folders.append(folder_info)
                logger.debug("Found agent folder: %s with files: %s", item.name, folder_info.files)
        return folders

    async def analyze_agents_with_llm(self, folders: list[AgentFolderInfo]) -> AgentDiscoveryResult:
        if not folders:
            return AgentDiscoveryResult(agents=[])

        folder_descriptions = "\n\n".join(folder.to_prompt_string() for folder in folders)
        try:
            if self.state_manager is None:
                raise RuntimeError("state_manager unavailable; cannot load discovery instructions")
            system_instruction = await self.state_manager.get_content_document(
                "instruction", "progena/agent_discovery_analysis_system.md"
            )
            user_instruction = await self.state_manager.get_content_document(
                "instruction", "progena/agent_discovery_analysis.md"
            )
            if system_instruction is None or user_instruction is None:
                raise RuntimeError("Missing agent discovery instruction(s) in Postgres")
            system_prompt = await render_runtime_instruction(self.state_manager, system_instruction)
            user_prompt = await render_runtime_instruction(
                self.state_manager, user_instruction, folder_descriptions=folder_descriptions
            )
            return await self.llm_client.chat_structured(
                prompt=user_prompt,
                response_model=AgentDiscoveryResult,
                schema_name="agent_discovery_result",
                system_prompt=system_prompt,
                temperature=0.1,
            )
        except Exception as exc:
            logger.error("LLM analysis failed: %s", exc)
            return self._fallback_extraction(folders)

    def _fallback_extraction(self, folders: list[AgentFolderInfo]) -> AgentDiscoveryResult:
        agents = []
        for folder in folders:
            if "http_agent.py" in folder.files:
                entrypoint = "http_agent.py"
                transport = "http"
            elif "agent.py" in folder.files:
                entrypoint = "agent.py"
                transport = "http"
            else:
                py_files = [filename for filename in folder.files if filename.endswith(".py")]
                if not py_files:
                    continue
                entrypoint = py_files[0]
                transport = "http"

            content = folder.file_contents.get(entrypoint, "")
            if "HTTPAgent" in content or "aiohttp" in content or "web.Application" in content:
                transport = "http"

            description = f"Agent from {folder.folder_name}"
            if content:
                import re

                match = re.search(r'"""(.*?)"""', content, re.DOTALL)
                if match:
                    doc = match.group(1).strip().split("\n")[0]
                    if doc:
                        description = doc

            agents.append(
                DiscoveredAgentSpec(
                    name=folder.folder_name,
                    description=description,
                    capabilities=[],
                    entrypoint=entrypoint,
                    transport=transport,
                    port=folder.manifest_port,
                    is_valid_agent=True,
                )
            )

        return AgentDiscoveryResult(agents=agents)

    async def discover_and_register(self) -> dict[str, Any]:
        logger.info("Starting agent discovery from %s", self.agents_dir)

        if hasattr(self.registry, "load"):
            await self.registry.load()

        existing_agents = []
        if hasattr(self.registry, "get_agents"):
            maybe_agents = self.registry.get_agents()
            if isawaitable(maybe_agents):
                maybe_agents = await maybe_agents
            if isinstance(maybe_agents, list):
                existing_agents = [agent for agent in maybe_agents if isinstance(agent, dict)]
        if existing_agents:
            registry_summary: dict[str, Any] = {
                "success": True,
                "agents_found": len(existing_agents),
                "agents_registered": 0,
                "agents_skipped": 0,
                "errors": None,
                "timestamp": datetime.now(UTC).isoformat(),
                "source": "registry",
            }
            try:
                refresh_summary = await refresh_agent_metadata(self.registry)
                if set(refresh_summary) == {"error"}:
                    registry_summary["registry_refresh"] = refresh_summary
                else:
                    registry_summary.update(refresh_summary)
            except Exception as exc:
                registry_summary["registry_refresh"] = {"error": str(exc)}
            return registry_summary

        folders = self.scan_agents_folder()
        logger.info("Found %d potential agent folders", len(folders))
        if not folders:
            return {
                "success": True,
                "agents_found": 0,
                "agents_registered": 0,
                "message": "No agent folders found",
            }

        result = await self.analyze_agents_with_llm(folders)

        # Apply declared manifest ports so an already-running A2A agent gets a
        # deterministic URL even when the LLM analysis did not infer one.
        ports_by_folder = {f.folder_name: f.manifest_port for f in folders if f.manifest_port}
        for spec in result.agents:
            self._apply_manifest_port(spec, ports_by_folder)

        registered = 0
        skipped = 0
        errors = []
        for agent_spec in result.agents:
            if not agent_spec.is_valid_agent:
                logger.info(
                    "Skipping invalid agent '%s': %s",
                    agent_spec.name,
                    agent_spec.reason_if_invalid,
                )
                skipped += 1
                continue

            try:
                await self._register_agent(agent_spec)
                registered += 1
                logger.info(
                    "Registered agent '%s' (%s transport)",
                    agent_spec.name,
                    agent_spec.transport,
                )
            except Exception as exc:
                logger.error("Failed to register agent '%s': %s", agent_spec.name, exc)
                errors.append(f"{agent_spec.name}: {exc}")

        summary: dict[str, Any] = {
            "success": len(errors) == 0,
            "agents_found": len(result.agents),
            "agents_registered": registered,
            "agents_skipped": skipped,
            "errors": errors if errors else None,
            "timestamp": result.scan_timestamp,
        }
        logger.info(
            "Agent discovery complete: %d found, %d registered, %d skipped",
            len(result.agents),
            registered,
            skipped,
        )

        try:
            refresh_summary = await refresh_agent_metadata(self.registry)
            if set(refresh_summary) == {"error"}:
                summary["registry_refresh"] = refresh_summary
            else:
                summary.update(refresh_summary)
        except Exception as exc:
            summary["registry_refresh"] = {"error": str(exc)}
        return summary

    def _apply_manifest_port(
        self, spec: DiscoveredAgentSpec, ports_by_folder: dict[str, int]
    ) -> None:
        """Fill a spec's port/url from its folder manifest when not already set."""
        if spec.url or spec.port:
            return
        port = ports_by_folder.get(spec.name)
        if port is None:
            normalized = spec.name.strip().lower().replace(" ", "_")
            port = ports_by_folder.get(normalized)
        if port is not None:
            spec.port = port

    async def _register_agent(self, spec: DiscoveredAgentSpec) -> None:
        from src.progena.registry.models import AgentRegistryEntry, AgentStatus

        entrypoint = spec.entrypoint
        port = spec.port
        url = spec.url.strip().rstrip("/") if spec.url else None
        if not url and port is not None:
            url = f"http://{self.host}:{port}"
        agent_card_url = spec.agent_card_url.strip() if spec.agent_card_url else None
        if not agent_card_url and url:
            agent_card_url = f"{url}/.well-known/agent-card.json"

        entry = AgentRegistryEntry(
            name=spec.name,
            kind="a2a_agent",
            version=spec.version,
            description=spec.description,
            capabilities=spec.capabilities,
            entrypoint=entrypoint,
            transport=spec.transport,
            port=port,
            url=url,
            agent_card_url=agent_card_url,
            status=AgentStatus.STOPPED,
        )
        await self.registry.register_agent(entry)


async def discover_agents(
    agents_dir: Path,
    llm_client: LLMClient,
    registry: ToolRegistry,
    host: str = "localhost",
    state_manager: Any = None,
) -> dict[str, Any]:
    """Convenience function to run agent discovery."""
    discovery = AgentDiscovery(
        agents_dir=agents_dir,
        llm_client=llm_client,
        registry=registry,
        host=host,
        state_manager=state_manager,
    )
    return await discovery.discover_and_register()
