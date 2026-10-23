"""Mcp document persistence."""

from __future__ import annotations

from typing import Any

from src.progena.persistence.documents import StateDocuments


class MCPStore:
    """Own mcp document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_mcp_config(self) -> dict[str, Any]:
        """Return the MCP configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the MCP config document is also created/edited by the
        backend (UI) writing the same Postgres document, so a cached copy
        would hide those cross-process changes from the runtime's periodic
        MCP refresh. See get_schedule for the same rationale.
        """
        from ..config import default_mcp_config_payload
        from ..mcp.loader import normalize_mcp_config_payload

        payload = await self._access.store.get_document("mcp", default_mcp_config_payload())
        self._access.cache["mcp"] = payload
        return normalize_mcp_config_payload(
            payload if isinstance(payload, dict) else default_mcp_config_payload()
        )

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        """Persist the MCP configuration document."""
        from ..mcp.loader import normalize_mcp_config_payload

        normalized = normalize_mcp_config_payload(payload)
        await self._access.save("mcp", normalized)

    async def get_mcp_tools(self) -> dict[str, Any]:
        """Return the runtime-owned discovered MCP tools document.

        Read straight from the shared store (not the cache): the backend (UI)
        toggles per-tool enabled flags by writing this same document, so a cached
        copy would hide those cross-process changes. See get_schedule.
        """
        default: dict[str, Any] = {"tools": []}
        payload = await self._access.store.get_document("mcp_tools", default)
        self._access.cache["mcp_tools"] = payload
        return payload if isinstance(payload, dict) else dict(default)

    async def save_mcp_tools(self, payload: dict[str, Any]) -> None:
        """Persist the runtime-owned discovered MCP tools document."""
        self._access.cache["mcp_tools"] = payload
        await self._access.store.save_document("mcp_tools", payload)
