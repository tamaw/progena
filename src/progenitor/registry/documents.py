"""Registry document persistence."""

from __future__ import annotations

from typing import Any

from src.progenitor.persistence.documents import StateDocuments


class RegistryStore:
    """Own registry document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_registry(self) -> dict[str, Any]:
        """Return the tool registry document."""
        registry = await self._access.get("registry")
        return dict(registry) if isinstance(registry, dict) else {}

    async def save_registry(self, registry: dict[str, Any]) -> None:
        """Persist the tool registry document."""
        await self._access.save("registry", registry)
