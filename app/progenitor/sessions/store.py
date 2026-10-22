"""Sessions document persistence."""

from __future__ import annotations

from typing import Any

from app.progenitor.documents.kinds import SESSION_KEY_PREFIX
from app.progenitor.persistence.documents import StateDocuments
from app.progenitor.persistence.logs import structured_log_message as _structured_log_message
from app.progenitor.sessions.models import ConversationMessage


class SessionStore:
    """Own sessions document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def log_conversation(self, message: ConversationMessage) -> None:
        """Log a conversation message to the audit trail.

        Messages live exclusively in the LangGraph checkpoint; this method
        only persists an audit copy in the runtime store.
        """
        payload = message.model_dump(mode="json")
        await self._access.store.write_log(
            source="conversation",
            message=_structured_log_message("conversation", payload),
            level="info",
            payload=payload,
            timestamp=payload.pop("timestamp", None),
            relative_path="conversation.jsonl",
        )

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
