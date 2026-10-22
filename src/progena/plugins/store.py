"""Plugins document persistence."""

from __future__ import annotations

import json
from typing import Any

from src.progena.persistence.documents import StateDocuments


class PluginStore:
    """Own plugins document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_plugins_config(self) -> dict[str, Any]:
        """Return the plugins configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the plugins config document is also created/edited by the
        reconciler and the backend, so a cached copy would hide those
        cross-process changes.
        """
        from ..config import default_plugins_config_payload

        payload = await self._access.store.get_document("plugins", default_plugins_config_payload())
        self._access.cache["plugins"] = payload
        return payload if isinstance(payload, dict) else default_plugins_config_payload()

    async def save_plugins_config(self, payload: dict[str, Any]) -> None:
        """Persist the plugins configuration document.

        Validates the payload against the plugin entry schema before saving,
        raising ValueError on invalid input so a violating edit is rejected
        and nothing changes.
        """
        from src.progena.plugins.schema import validate_plugins_document

        validate_plugins_document(json.dumps(payload))
        await self._access.save("plugins", payload)

    async def get_marketplaces_config(self) -> dict[str, Any]:
        """Return the marketplaces configuration document.

        Read straight from the shared store rather than the in-memory
        cache: the backend also reads/writes this same Postgres document
        directly, so a cached copy would hide those cross-process changes.
        """
        from ..config import default_marketplaces_config_payload

        payload = await self._access.store.get_document(
            "marketplaces", default_marketplaces_config_payload()
        )
        self._access.cache["marketplaces"] = payload
        return payload if isinstance(payload, dict) else default_marketplaces_config_payload()

    async def save_marketplaces_config(self, payload: dict[str, Any]) -> None:
        """Persist the marketplaces configuration document.

        Validates the payload against the marketplace entry schema before
        saving, raising ValueError on invalid input so a violating edit is
        rejected and nothing changes.
        """
        from src.progena.plugins.marketplaces import validate_marketplaces_document

        validate_marketplaces_document(json.dumps(payload))
        await self._access.save("marketplaces", payload)
