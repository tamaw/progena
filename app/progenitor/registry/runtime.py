"""Runtime owner for the registry store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import ToolRegistry


class AgentRegistryRuntime:
    """Owns the runtime registry (database-backed, with a JSON-file fallback)."""

    def __init__(self, config_dir: Path, project_root: Path) -> None:
        self.config_dir = Path(config_dir)
        self.project_root = Path(project_root)
        self.agent_registry = ToolRegistry(self.config_dir / "agents.json")

    def bind_state_manager(self, state_manager: Any) -> None:
        """Back the registry with the StateManager's ``registry`` document.

        This makes the database the canonical store; ``agents.json`` becomes a
        one-time migration source only. Call before :meth:`load`.
        """
        self.agent_registry.bind_store(
            state_manager.get_registry,
            state_manager.save_registry,
        )

    async def load(self) -> None:
        await self.agent_registry.load()
