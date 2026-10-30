"""Read access to ``channel:<id>`` documents for the runtime.

The backend owns channel writes (it validates type-specific settings and
encrypts secrets); the runtime only reads, always through the redacted view.
"""

from __future__ import annotations

from typing import Any

from src.progena.channels.contracts import channel_id_from_key, redacted_channel_document
from src.progena.documents.kinds import CHANNEL_KEY_PREFIX
from src.progena.persistence.documents import StateDocuments


class ChannelStore:
    """Own channel document reads."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        """One channel document with credentials redacted, or ``None``."""
        if channel_id_from_key(f"{CHANNEL_KEY_PREFIX}{channel_id}") is None:
            return None
        payload = await self._access.store.peek_document(f"{CHANNEL_KEY_PREFIX}{channel_id}")
        return redacted_channel_document(payload) if isinstance(payload, dict) else None

    async def list_channels(self) -> dict[str, dict[str, Any]]:
        """Every channel document by id, with credentials redacted."""
        rows = await self._access.store.list_documents(CHANNEL_KEY_PREFIX)
        channels: dict[str, dict[str, Any]] = {}
        for key, payload in rows:
            channel_id = channel_id_from_key(key)
            if channel_id is not None and isinstance(payload, dict):
                channels[channel_id] = redacted_channel_document(payload)
        return channels
