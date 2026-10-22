"""Alerts document persistence."""

from __future__ import annotations

from typing import Any

from src.progena.documents.kinds import ALERT_KEY_PREFIX
from src.progena.persistence.documents import StateDocuments


class AlertStore:
    """Own alerts document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def save_alert(self, alert: dict[str, Any]) -> None:
        """Persist one new alert document (see ``src.progena.alerts.contracts``)."""
        await self._access.store.save_document(f"{ALERT_KEY_PREFIX}{alert['id']}", alert)

    async def list_alerts(self) -> list[dict[str, Any]]:
        """Return every alert document, newest first."""
        rows = await self._access.store.list_documents(ALERT_KEY_PREFIX)
        alerts = [payload for _, payload in rows if isinstance(payload, dict)]
        return sorted(alerts, key=lambda alert: alert.get("timestamp") or 0, reverse=True)
