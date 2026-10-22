"""User alerts: one ``alert:<id>`` document each.

Every producer writes an alert document built here. The backend consumes the
``alert`` document events: it shows the alert in the alert center and asks
Progenitor to relay it to the user (chat, or Telegram when away).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from app.progenitor.documents.kinds import ALERT_KEY_PREFIX

ALERT_LEVELS = ("info", "warning", "error", "critical")


def alert_key(alert_id: str) -> str:
    return f"{ALERT_KEY_PREFIX}{alert_id}"


def normalize_alert_type(value: str | None) -> str:
    normalized = str(value or "info").strip().lower()
    return normalized if normalized in ALERT_LEVELS else "info"


def build_alert(
    *,
    title: str,
    message: str,
    level: str = "info",
    urgent: bool | None = None,
    requires_response: bool = False,
    source: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A new, unread alert payload. Warnings and errors are urgent unless stated."""

    alert_type = normalize_alert_type(level)
    payload: dict[str, Any] = {
        "id": f"alert-{uuid.uuid4().hex[:12]}",
        "type": alert_type,
        "title": title.strip() or "Alert",
        "message": message,
        "timestamp": time.time(),
        "urgent": alert_type != "info" if urgent is None else urgent,
        "requires_response": requires_response,
        "viewed": False,
        "viewed_at": None,
    }
    if source:
        payload["source"] = source
    if context:
        payload["context"] = context
    return payload
