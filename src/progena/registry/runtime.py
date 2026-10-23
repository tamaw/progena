"""Runtime owner for the registry store."""

from __future__ import annotations

from typing import Any

from .store import ToolRegistry


class AgentRegistryRuntime:
    """Owns the runtime registry (database-backed via StateManager)."""

    def __init__(self) -> None:
        self.agent_registry = ToolRegistry()

    def bind_state_manager(self, state_manager: Any) -> None:
        """Back the registry with the StateManager's ``registry`` document.

        This makes the database the canonical store. Call before :meth:`load`.
        """
        self.agent_registry.bind_store(
            state_manager.get_registry,
            state_manager.save_registry,
        )

    async def load(self) -> None:
        await self.agent_registry.load()
