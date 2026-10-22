"""Repositories document persistence."""

from __future__ import annotations

from typing import Any

from src.progenitor.documents.kinds import REPO_KEY_PREFIX
from src.progenitor.persistence.documents import StateDocuments


class RepositoryStore:
    """Own repositories document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_repo(self, repo_id: str) -> dict[str, Any] | None:
        """Return one declared repo entry, or ``None`` if it isn't declared."""
        payload = await self._access.store.peek_document(f"{REPO_KEY_PREFIX}{repo_id}")
        return dict(payload) if isinstance(payload, dict) else None

    async def list_repos(self) -> dict[str, dict[str, Any]]:
        """Return every declared repo entry, keyed by repo id."""
        rows = await self._access.store.list_documents(REPO_KEY_PREFIX)
        return {
            key.removeprefix(REPO_KEY_PREFIX): dict(payload)
            for key, payload in rows
            if isinstance(payload, dict)
        }

    async def save_repo(self, repo_id: str, entry: dict[str, Any]) -> None:
        """Persist one declared repo entry (uncached; the backend writes these too)."""
        await self._access.store.save_document(f"{REPO_KEY_PREFIX}{repo_id}", entry)

    async def delete_repo(self, repo_id: str) -> None:
        """Remove one declared repo entry."""
        await self._access.store.delete_document(f"{REPO_KEY_PREFIX}{repo_id}")

    async def get_repo_scan(self) -> dict[str, Any]:
        """Return the heartbeat's last derived git-state scan."""
        scan = await self._access.get("repo_scan")
        return dict(scan) if isinstance(scan, dict) else {}

    async def save_repo_scan(self, payload: dict[str, Any]) -> None:
        """Persist the heartbeat's derived git-state scan."""
        await self._access.save("repo_scan", payload)
