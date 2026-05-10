"""Runtime owner for the registry store."""

from __future__ import annotations

from pathlib import Path

from .store import ToolRegistry


class AgentRegistryRuntime:
    """Owns the file-backed runtime registry."""

    def __init__(self, config_dir: Path, project_root: Path) -> None:
        self.config_dir = Path(config_dir)
        self.project_root = Path(project_root)
        self.agent_registry = ToolRegistry(self.config_dir / "agents.json")

    async def load(self) -> None:
        await self.agent_registry.load()
