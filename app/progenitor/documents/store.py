"""Documents document persistence."""

from __future__ import annotations

from typing import Any

from app.progenitor.documents.content import content_document_key, content_document_prefix
from app.progenitor.documents.kinds import DOCUMENT_TYPE_KEY_PREFIX, MODULE_STATUS_KEY_PREFIX
from app.progenitor.documents.types import (
    DocumentType,
    builtin_document_type_rows,
    parse_document_types,
)
from app.progenitor.persistence.documents import StateDocuments
from app.progenitor.utils import utcnow_isoformat


class DocumentStore:
    """Own documents document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def list_document_types(self) -> dict[str, DocumentType]:
        """Return the ``document_type:<id>`` registry, keyed by type id."""
        rows = await self._access.store.list_documents(DOCUMENT_TYPE_KEY_PREFIX)
        return parse_document_types(payload for _, payload in rows)

    async def _seed_document_types(self) -> None:
        """Write the built-in document types, touching only rows that differ."""
        for key, payload in builtin_document_type_rows():
            if await self._access.store.peek_document(key) != payload:
                await self._access.store.save_document(key, payload)

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        """Return one content document's text, or ``None`` if missing/malformed."""
        key = content_document_key(kind, relative_path)
        payload = await self._access.store.peek_document(key)
        if isinstance(payload, dict) and isinstance((content := payload.get("content")), str):
            return content
        return None

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        """Persist one content document's text."""
        key = content_document_key(kind, relative_path)
        await self._access.save(key, {"content": content, "updated_at": utcnow_isoformat()})

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        """Remove one content document."""
        key = content_document_key(kind, relative_path)
        await self._access.store.delete_document(key)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        """Return ``(relative_path, content)`` for every document of one kind."""
        prefix = content_document_prefix(kind)
        rows = await self._access.store.list_documents(prefix)
        results: list[tuple[str, str]] = []
        for key, payload in rows:
            if isinstance(payload, dict) and isinstance(payload.get("content"), str):
                results.append((key[len(prefix) :], payload["content"]))
        return results

    async def get_module_status(self, name: str) -> dict[str, Any] | None:
        """Return a module host's last reported status, if it is well-formed."""
        payload = await self._access.store.peek_document(f"{MODULE_STATUS_KEY_PREFIX}{name}")
        return payload if isinstance(payload, dict) else None
