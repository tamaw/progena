"""Sessions document persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.persistence.documents import StateDocuments
from src.progena.sessions.status import SessionTurnStatus, terminal_session_fields


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

    async def record_turn_terminal(self, thread_id: str, status: SessionTurnStatus) -> bool:
        """Persist terminal flags and activity without replacing other session fields."""
        return await self.merge_session(
            thread_id,
            {**terminal_session_fields(status), "last_activity": datetime.now(UTC).isoformat()},
        )
