"""Inbox document persistence."""

from __future__ import annotations

from typing import Any

from src.progenitor.documents.kinds import INBOX_MESSAGE_KEY_PREFIX
from src.progenitor.persistence.documents import StateDocuments


class InboxStore:
    """Own inbox document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    @staticmethod
    def _inbox_message_key(message_id: str) -> str:
        return f"{INBOX_MESSAGE_KEY_PREFIX}{message_id}"

    async def get_inbox_message(self, message_id: str) -> dict[str, Any] | None:
        """Return one inbox message document, or ``None`` if it does not exist.

        Reads straight from the shared store (never the cache, and never
        creating a default row): the backend mutates these same rows
        (mark-read/delete), and the message list is a key-prefix scan a junk
        row would pollute.
        """
        key = self._inbox_message_key(message_id)
        payload = await self._access.store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_inbox_message(self, message_id: str, message: dict[str, Any]) -> None:
        """Persist one inbox message document (no index to maintain)."""
        key = self._inbox_message_key(message_id)
        await self._access.save(key, message)

    async def delete_inbox_message(self, message_id: str) -> None:
        """Remove one inbox message document."""
        key = self._inbox_message_key(message_id)
        await self._access.store.delete_document(key)

    async def list_inbox_messages(self) -> list[dict[str, Any]]:
        """Return all inbox message documents (unsorted; callers order them)."""
        rows = await self._access.store.list_documents(INBOX_MESSAGE_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]
