"""Settings document persistence."""

from __future__ import annotations

from typing import Any

from src.progena.persistence.documents import StateDocuments
from src.progena.settings.model_profiles import default_model_profiles_payload
from src.progena.settings.preferences import default_preferences_payload


class SettingsStore:
    """Own settings document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_preferences_config(self) -> dict[str, Any]:
        """Return the preferences document from the shared runtime store.

        The backend edits this same Postgres document (Settings page), so read
        directly from the store rather than the in-memory cache to preserve
        cross-process changes.
        """
        default = default_preferences_payload()
        payload = await self._access.store.get_document("preferences", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_preferences_config(self, payload: dict[str, Any]) -> None:
        """Persist the preferences document to the shared runtime store."""
        await self._access.save("preferences", payload)

    async def get_model_profiles_config(self) -> dict[str, Any]:
        """Return the shared model profile map behind ``/config/profiles.json``."""
        default = default_model_profiles_payload()
        payload = await self._access.store.get_document("profile", default)
        return payload if isinstance(payload, dict) else default

    async def save_model_profiles_config(self, payload: dict[str, Any]) -> None:
        """Persist the validated shared model profile map."""
        await self._access.save("profile", payload)

    async def get_execution_policy(self, policy_id: str) -> dict[str, Any] | None:
        from src.progena.settings.policies import POLICY_KEY_PREFIX

        payload = await self._access.store.peek_document(f"{POLICY_KEY_PREFIX}{policy_id}")
        return payload if isinstance(payload, dict) else None

    async def save_execution_policy(self, policy_id: str, payload: dict[str, Any]) -> None:
        from src.progena.settings.policies import POLICY_KEY_PREFIX, validate_policy

        policy = validate_policy(payload)
        if policy.id != policy_id:
            raise ValueError("Policy id must match filename")
        await self._access.save(f"{POLICY_KEY_PREFIX}{policy_id}", policy.model_dump(mode="json"))

    async def delete_execution_policy(self, policy_id: str) -> None:
        from src.progena.settings.policies import POLICY_KEY_PREFIX

        await self._access.store.delete_document(f"{POLICY_KEY_PREFIX}{policy_id}")

    async def list_execution_policies(self) -> list[dict[str, Any]]:
        from src.progena.settings.policies import POLICY_KEY_PREFIX

        rows = await self._access.store.list_documents(POLICY_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]
