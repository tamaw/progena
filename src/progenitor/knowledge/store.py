"""Knowledge document persistence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.progenitor.knowledge.indexer_config import (
    INDEX_STATUS_DOC_KEY,
    INDEXER_DOC_KEY,
    default_indexer_payload,
    validate_indexer_payload,
)
from src.progenitor.knowledge.pages import knowledge_page_key
from src.progenitor.persistence.documents import StateDocuments
from src.progenitor.utils import utcnow_isoformat


class KnowledgeStore:
    """Own knowledge document operations."""

    def __init__(
        self,
        access: StateDocuments,
        get_model_profiles_config: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        self._access = access
        self._get_model_profiles_config = get_model_profiles_config

    async def get_knowledge_config(self) -> dict[str, Any]:
        """Return the knowledge document from the shared runtime store.

        The backend edits this same Postgres document, so read directly from the
        store rather than the in-memory cache to preserve cross-process changes.
        """
        default: dict[str, Any] = {"bases": []}
        payload = await self._access.store.get_document("knowledge", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_knowledge_config(self, payload: dict[str, Any]) -> None:
        """Persist the knowledge document to the shared runtime store."""
        await self._access.save("knowledge", payload)

    async def get_indexer_config(self) -> dict[str, Any]:
        """Return the search-indexer settings behind ``/config/indexer.json``."""
        default = default_indexer_payload()
        payload = await self._access.store.get_document(INDEXER_DOC_KEY, default)
        return payload if isinstance(payload, dict) else default

    async def save_indexer_config(self, payload: dict[str, Any]) -> None:
        """Persist indexer settings; the profile must name a ``/config/profile.json`` entry."""
        profiles = await self._get_model_profiles_config()
        validated = validate_indexer_payload(payload, profile_names=profiles.keys())
        await self._access.save(INDEXER_DOC_KEY, validated)

    async def get_knowledge_index(self) -> dict[str, Any]:
        """Return the backend indexer's status document (``/knowledge/indexer.json``)."""
        payload = await self._access.store.peek_document(INDEX_STATUS_DOC_KEY)
        return payload if isinstance(payload, dict) else {"embedding": None, "bases": {}}

    async def get_knowledge_page(self, base_id: str, path: str) -> dict[str, Any] | None:
        payload = await self._access.store.peek_document(knowledge_page_key(base_id, path))
        if isinstance(payload, dict) and isinstance(payload.get("content"), str):
            return payload
        return None

    async def save_knowledge_page(self, base_id: str, path: str, content: str) -> None:
        await self._access.save(
            knowledge_page_key(base_id, path),
            {"content": content, "updated_at": utcnow_isoformat()},
        )

    async def list_knowledge_pages(self) -> list[tuple[str, str, dict[str, Any]]]:
        rows = await self._access.store.list_documents("knowledge:")
        result: list[tuple[str, str, dict[str, Any]]] = []
        for key, payload in rows:
            remainder = key[len("knowledge:") :]
            base_id, separator, path = remainder.partition("/")
            if (
                separator
                and base_id
                and path
                and isinstance(payload, dict)
                and isinstance(payload.get("content"), str)
            ):
                result.append((base_id, path, payload))
        return result
