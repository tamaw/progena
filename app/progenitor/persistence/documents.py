"""Shared document access; domains own interpretation and mutations."""

from dataclasses import dataclass
from typing import Any

from .store import LangGraphRuntimeStore


@dataclass
class StateDocuments:
    """One cache and native store shared by the runtime's domain stores."""

    store: LangGraphRuntimeStore
    cache: dict[str, Any]
    database_url: str | None
    read_through: frozenset[str]

    async def get(self, name: str) -> Any:
        if name in self.read_through:
            return await self.store.get_document(name, {})
        if name not in self.cache:
            self.cache[name] = await self.store.get_document(name, {})
        return self.cache[name]

    async def save(self, name: str, payload: Any) -> None:
        if name not in self.read_through:
            self.cache[name] = payload
        await self.store.save_document(name, payload)
