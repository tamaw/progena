"""Backend-managed alert records and helpers."""

from __future__ import annotations

import time
from typing import TypedDict

from pydantic import BaseModel

MAX_ALERTS = 100


class AlertInfo(BaseModel):
    """Alert information stored by the backend."""

    id: str
    type: str
    title: str
    message: str
    timestamp: float
    urgent: bool = False
    requires_response: bool = False
    viewed: bool = False
    viewed_at: float | None = None


class AlertsResponse(BaseModel):
    """Response containing alert history."""

    alerts: list[AlertInfo]
    total: int
    unread: int


class MarkAlertsViewedRequest(BaseModel):
    """Request payload for marking alerts viewed."""

    alert_ids: list[str] | None = None


class MarkAlertsViewedResponse(BaseModel):
    """Response payload after marking alerts viewed."""

    updated: int
    unread: int


class AlertSummary(TypedDict):
    """Aggregate alert data for status payloads."""

    total: int
    unread: int
    latest_timestamp: float | None


_alerts: list[AlertInfo] = []
_alert_id_counter = 0


def generate_alert_id() -> str:
    """Generate a unique alert ID."""
    global _alert_id_counter
    _alert_id_counter += 1
    return f"alert-{int(time.time())}-{_alert_id_counter}"


def normalize_alert_type(value: str | None) -> str:
    """Normalize alert severity/type for storage and UI display."""
    normalized = str(value or "info").strip().lower()
    return normalized or "info"


def create_alert(
    *,
    type: str,
    title: str,
    message: str,
    urgent: bool = False,
    requires_response: bool = False,
) -> AlertInfo:
    """Create and store a new backend-managed alert."""
    alert = AlertInfo(
        id=generate_alert_id(),
        type=normalize_alert_type(type),
        title=title,
        message=message,
        timestamp=time.time(),
        urgent=urgent,
        requires_response=requires_response,
    )
    _alerts.append(alert)

    while len(_alerts) > MAX_ALERTS:
        _alerts.pop(0)

    return alert


def list_alerts() -> list[AlertInfo]:
    """Return alert history with newest alerts first."""
    return list(reversed(_alerts))


def get_alert(alert_id: str) -> AlertInfo | None:
    """Return a single alert by ID."""
    return next((alert for alert in _alerts if alert.id == alert_id), None)


def unread_alert_count() -> int:
    """Return the number of unread alerts."""
    return sum(1 for alert in _alerts if not alert.viewed)


def get_alerts_response() -> AlertsResponse:
    """Build the API response payload for alert history."""
    alerts = list_alerts()
    return AlertsResponse(alerts=alerts, total=len(alerts), unread=unread_alert_count())


def alert_summary() -> AlertSummary:
    """Return aggregate alert data for status endpoints."""
    latest = _alerts[-1].timestamp if _alerts else None
    return {
        "total": len(_alerts),
        "unread": unread_alert_count(),
        "latest_timestamp": latest,
    }


def mark_alerts_viewed(alert_ids: list[str] | None = None) -> int:
    """Mark matching alerts viewed. If no IDs are provided, mark all alerts viewed."""
    target_ids = set(alert_ids or [])
    updated = 0
    viewed_at = time.time()

    for alert in _alerts:
        if alert.viewed:
            continue
        if target_ids and alert.id not in target_ids:
            continue
        alert.viewed = True
        alert.viewed_at = viewed_at
        updated += 1

    return updated


def clear_alerts() -> None:
    """Reset in-memory alert storage for tests."""
    global _alert_id_counter
    _alerts.clear()
    _alert_id_counter = 0
