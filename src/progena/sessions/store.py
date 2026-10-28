"""Sessions document persistence."""

from __future__ import annotations

from typing import Any

from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.persistence.documents import StateDocuments


class SessionStore:
    """Own sessions document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_session(self, thread_id: str) -> dict[str, Any] | None:
        """Return ``session:<thread_id>``'s payload, or ``None`` if the thread has none."""
        return await self._access.store.peek_document(f"{SESSION_KEY_PREFIX}{thread_id}")

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return every session document payload (unsorted)."""
        return [
            payload for _, payload in await self._access.store.list_documents(SESSION_KEY_PREFIX)
        ]

    async def merge_session(self, thread_id: str, fields: dict[str, Any]) -> bool:
        """Atomically merge ``fields`` into ``session:<thread_id>`` payload.

        Returns ``True`` when the row existed.
        """
        return await self._access.store.merge_document(f"{SESSION_KEY_PREFIX}{thread_id}", fields)
