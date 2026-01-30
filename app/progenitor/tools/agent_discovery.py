"""
Agent Discovery Tool - Discovers and registers agents from the agents folder.

Scans app/agents/ directory, uses LLM with structured output to understand
each agent's purpose and capabilities, then updates the registry.

This runs on Progenitor startup and can be invoked as a tool to re-scan agents.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..llm.client import LLMClient
    from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# Folders to skip when scanning
SKIP_FOLDERS = {"__pycache__", ".git", "base", ".venv", "node_modules"}

# Files to skip
SKIP_FILES = {"__init__.py", ".gitkeep", ".DS_Store"}


class DiscoveredAgentSpec(BaseModel):
    """Structured output for an agent discovered from source files."""

    name: str = Field(description="The agent's name (derived from folder name or code)")
    description: str = Field(description="A brief description of what this agent does")
    capabilities: list[str] = Field(
        default_factory=list,
        description="List of capabilities this agent provides (e.g., 'greeting', 'file_operations')",
    )
    entrypoint: str = Field(
        description="The main Python file to run this agent (e.g., 'agent.py' or 'http_agent.py')"
    )
    transport: str = Field(
        default="http",
        description="Transport protocol: 'http' (preferred) or 'stdio' (legacy)",
    )
    version: str = Field(default="0.1.0", description="Agent version")
    is_valid_agent: bool = Field(
        default=True,
        description="Whether this folder contains a valid runnable agent",
    )
    reason_if_invalid: Optional[str] = Field(
        default=None,
        description="If not a valid agent, explain why",
    )


class AgentDiscoveryResult(BaseModel):
    """Structured output for the full discovery process."""

    agents: list[DiscoveredAgentSpec] = Field(
        default_factory=list,
        description="List of discovered agents",
    )
    scan_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="When the scan was performed",
    )


@dataclass
class AgentFolderInfo:
    """Information about an agent folder collected during scan."""

    folder_name: str
    folder_path: Path
    files: list[str] = field(default_factory=list)
    file_contents: dict[str, str] = field(default_factory=dict)

    def to_prompt_string(self) -> str:
        """Convert to a string suitable for LLM prompt."""
        lines = [f"## Agent Folder: {self.folder_name}"]
        lines.append(f"Path: {self.folder_path}")
        lines.append(f"Files: {', '.join(self.files)}")
        lines.append("")

        for filename, content in self.file_contents.items():
            # Truncate very long files
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            lines.append(f"### {filename}")
            lines.append("```python")
            lines.append(content)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)


class AgentDiscovery:
    """
    Discovers agents from the agents/ folder and registers them.

    Uses LLM with structured output to understand each agent's
    purpose and capabilities from the source code.
    """

    def __init__(
        self,
        agents_dir: Path,
        llm_client: "LLMClient",
        registry: "ToolRegistry",
        host: str = "localhost",
    ):
        """
        Initialize the agent discovery tool.

        Args:
            agents_dir: Path to the agents/ directory.
            llm_client: LLM client for structured extraction.
            registry: Tool registry to update.
            host: Host for agent endpoints.
        """
        self.agents_dir = Path(agents_dir)
        self.llm_client = llm_client
        self.registry = registry
        self.host = host

    def scan_agents_folder(self) -> list[AgentFolderInfo]:
        """
        Scan the agents directory for agent folders.

        Returns:
            List of AgentFolderInfo for each discovered folder.
        """
        if not self.agents_dir.exists():
            logger.warning("Agents directory does not exist: %s", self.agents_dir)
            return []

        folders = []

        for item in self.agents_dir.iterdir():
            if not item.is_dir():
                continue

            if item.name in SKIP_FOLDERS:
                continue

            # Collect folder info
            folder_info = AgentFolderInfo(
                folder_name=item.name,
                folder_path=item,
            )

            # Get files in folder
            for file_item in item.iterdir():
                if file_item.is_file() and file_item.name not in SKIP_FILES:
                    folder_info.files.append(file_item.name)

                    # Read Python files for LLM analysis
                    if file_item.suffix == ".py":
                        try:
                            content = file_item.read_text(encoding="utf-8")
                            folder_info.file_contents[file_item.name] = content
                        except Exception as e:
                            logger.warning(
                                "Failed to read %s: %s", file_item, e
                            )

            # Only include folders with Python files
            if folder_info.file_contents:
                folders.append(folder_info)
                logger.debug("Found agent folder: %s with files: %s",
                           item.name, folder_info.files)

        return folders

    async def analyze_agents_with_llm(
        self,
        folders: list[AgentFolderInfo],
    ) -> AgentDiscoveryResult:
        """
        Use LLM to analyze agent folders and extract metadata.

        Args:
            folders: List of agent folder info from scan.

        Returns:
            AgentDiscoveryResult with structured agent specs.
        """
        if not folders:
            return AgentDiscoveryResult(agents=[])

        # Build prompt with all folder info
        folder_descriptions = "\n\n".join(f.to_prompt_string() for f in folders)

        system_prompt = """You are an expert at analyzing Python code to understand agent implementations.

Given a set of agent folders with their source code, extract structured information about each agent.

For each folder:
1. Determine if it's a valid runnable agent (has a main entrypoint)
2. Extract the agent's name (from the code or folder name)
3. Write a clear description of what the agent does
4. List its capabilities (what it can do)
5. Identify the entrypoint file (prefer http_agent.py over agent.py if both exist)
6. Determine the transport (http if it uses HTTPAgent/aiohttp, stdio otherwise)

If a folder is NOT a valid agent (e.g., just utility code), mark is_valid_agent=false.

Prefer HTTP transport when the agent uses HTTPAgent, aiohttp, or starts a web server.
Capabilities should be short verb phrases like "greeting", "file_operations", "code_analysis".
"""

        user_prompt = f"""Analyze these agent folders and extract structured information for each valid agent:

{folder_descriptions}

Return a JSON object with an "agents" array containing information for each folder analyzed.
For folders that are valid agents, include full details.
For folders that are not valid agents, set is_valid_agent=false with a reason."""

        try:
            # Import here to avoid circular imports
            from ..llm.client import _build_json_schema_format

            response_format = _build_json_schema_format(
                AgentDiscoveryResult, "agent_discovery_result"
            )

            response = await self.llm_client.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system_prompt=system_prompt,
                response_format=response_format,
                temperature=0.1,  # Low temperature for structured extraction
            )

            # Parse the response
            import json
            result_data = json.loads(response)
            return AgentDiscoveryResult(**result_data)

        except Exception as e:
            logger.error("LLM analysis failed: %s", e)
            # Fall back to basic extraction
            return self._fallback_extraction(folders)

    def _fallback_extraction(
        self,
        folders: list[AgentFolderInfo],
    ) -> AgentDiscoveryResult:
        """
        Fallback extraction without LLM when it fails.

        Uses simple heuristics to extract agent info.
        """
        agents = []

        for folder in folders:
            # Determine entrypoint (prefer http_agent.py)
            if "http_agent.py" in folder.files:
                entrypoint = "http_agent.py"
                transport = "http"
            elif "agent.py" in folder.files:
                entrypoint = "agent.py"
                transport = "stdio"  # Default for legacy agent.py
            else:
                # Look for any Python file
                py_files = [f for f in folder.files if f.endswith(".py")]
                if py_files:
                    entrypoint = py_files[0]
                    transport = "stdio"
                else:
                    continue

            # Check if it's an HTTP agent by looking for keywords
            content = folder.file_contents.get(entrypoint, "")
            if "HTTPAgent" in content or "aiohttp" in content or "web.Application" in content:
                transport = "http"

            # Extract description from docstring
            description = f"Agent from {folder.folder_name}"
            if content:
                # Try to extract first docstring
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
                    is_valid_agent=True,
                )
            )

        return AgentDiscoveryResult(agents=agents)

    async def discover_and_register(self, clear_existing: bool = True) -> dict[str, Any]:
        """
        Main entry point: scan, analyze, and register all agents.

        Args:
            clear_existing: If True, clear existing agents before registering new ones.
                           This makes the operation a full rescan rather than additive.

        Returns:
            Summary of the discovery process.
        """
        logger.info("Starting agent discovery from %s", self.agents_dir)

        # Clear existing agents if requested (full rescan mode)
        if clear_existing:
            logger.info("Clearing existing agents from registry")
            await self.registry.clear_agents()

        # Scan folders
        folders = self.scan_agents_folder()
        logger.info("Found %d potential agent folders", len(folders))

        if not folders:
            # Save the empty registry to persist the clear
            await self.registry.save()
            return {
                "success": True,
                "agents_found": 0,
                "agents_registered": 0,
                "message": "No agent folders found",
            }

        # Analyze with LLM
        result = await self.analyze_agents_with_llm(folders)

        # Register valid agents
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
            except Exception as e:
                logger.error("Failed to register agent '%s': %s", agent_spec.name, e)
                errors.append(f"{agent_spec.name}: {e}")

        summary = {
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

        return summary

    async def _register_agent(self, spec: DiscoveredAgentSpec) -> None:
        """Register a discovered agent in the registry."""
        from ..state.models import AgentRegistryEntry, AgentStatus

        # Build full entrypoint path
        entrypoint = f"app/agents/{spec.name}/{spec.entrypoint}"

        # Allocate port for HTTP agents
        port = None
        endpoint = None
        agent_card_url = None

        if spec.transport == "http":
            port = self.registry.allocate_port()
            endpoint = f"http://{self.host}:{port}"
            agent_card_url = f"{endpoint}/.well-known/agent.json"

        entry = AgentRegistryEntry(
            name=spec.name,
            kind="a2a_agent",
            version=spec.version,
            description=spec.description,
            capabilities=spec.capabilities,
            entrypoint=entrypoint,
            transport=spec.transport,
            port=port,
            endpoint=endpoint,
            agent_card_url=agent_card_url,
            status=AgentStatus.STOPPED,
        )

        await self.registry.register_agent(entry)


async def discover_agents(
    agents_dir: Path,
    llm_client: "LLMClient",
    registry: "ToolRegistry",
    host: str = "localhost",
) -> dict[str, Any]:
    """
    Convenience function to run agent discovery.

    Args:
        agents_dir: Path to agents/ directory.
        llm_client: LLM client for analysis.
        registry: Tool registry to update.
        host: Host for agent endpoints.

    Returns:
        Discovery summary dict.
    """
    discovery = AgentDiscovery(
        agents_dir=agents_dir,
        llm_client=llm_client,
        registry=registry,
        host=host,
    )
    return await discovery.discover_and_register()
