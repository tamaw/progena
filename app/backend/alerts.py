"""Backend access to ``alert:<id>`` documents (the alert center).

Alerts are durable documents (see :mod:`app.progenitor.alerts`); this module
reads them for the alert center, marks them viewed, creates backend-side ones
(workflow notifications) and keeps only the newest :data:`MAX_ALERTS`.
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from pydantic import BaseModel

from app.progenitor.alerts import alert_key, build_alert
from app.progenitor.document_kinds import ALERT_KEY_PREFIX

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store

MAX_ALERTS = 100


class AlertInfo(BaseModel):
    """One alert as the alert center shows it."""

    id: str
    type: str
    title: str
    message: str
    timestamp: float
    urgent: bool = False
    requires_response: bool = False
    viewed: bool = False
    viewed_at: float | None = None
    source: str | None = None
    context: dict[str, Any] | None = None


class AlertsResponse(BaseModel):
    """Response containing alert history."""

    alerts: list[AlertInfo]
    total: int
    unread: int


class MarkAlertsViewedRequest(BaseModel):
    """Request payload for marking alerts viewed."""

    alert_ids: list[str] | None = None


class AlertSummary(TypedDict):
    """Aggregate alert data for status payloads."""

    total: int
    unread: int
    latest_timestamp: float | None


def create_alert(
    *,
    type: str,
    title: str,
    message: str,
    urgent: bool | None = None,
    requires_response: bool = False,
    source: str | None = None,
    context: dict[str, Any] | None = None,
) -> AlertInfo:
    """Write a new alert document; its document event drives display and relay."""
    payload = build_alert(
        title=title,
        message=message,
        level=type,
        urgent=urgent,
        requires_response=requires_response,
        source=source,
        context=context,
    )
    store = get_default_document_store()
    store.save_document(PROGENITOR_STATE_PREFIX, alert_key(payload["id"]), payload)
    _prune(store)
    return AlertInfo.model_validate(payload)


def _all_alerts() -> list[AlertInfo]:
    alerts = [
        AlertInfo.model_validate(document.payload)
        for _key, document in get_default_document_store().list_documents(
            PROGENITOR_STATE_PREFIX, ALERT_KEY_PREFIX
        )
        if isinstance(document.payload, dict)
    ]
    return sorted(alerts, key=lambda alert: alert.timestamp, reverse=True)


def _prune(store: Any) -> None:
    for stale in _all_alerts()[MAX_ALERTS:]:
        store.delete_document(PROGENITOR_STATE_PREFIX, alert_key(stale.id))


def list_alerts() -> list[AlertInfo]:
    """Return alert history with newest alerts first."""
    return _all_alerts()


def get_alert(alert_id: str) -> AlertInfo | None:
    """Return a single alert by ID."""
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, alert_key(alert_id), None
    )
    if document is None or not isinstance(document.payload, dict):
        return None
    return AlertInfo.model_validate(document.payload)


def get_alerts_response() -> AlertsResponse:
    """Build the API response payload for alert history."""
    alerts = list_alerts()
    return AlertsResponse(
        alerts=alerts, total=len(alerts), unread=sum(1 for a in alerts if not a.viewed)
    )


def alert_summary() -> AlertSummary:
    """Return aggregate alert data for status endpoints."""
    alerts = list_alerts()
    return {
        "total": len(alerts),
        "unread": sum(1 for alert in alerts if not alert.viewed),
        "latest_timestamp": alerts[0].timestamp if alerts else None,
    }


class _AlertGone(Exception):
    """The alert was deleted (pruned) between listing and marking it."""


def mark_alerts_viewed(alert_ids: list[str] | None = None) -> int:
    """Mark matching alerts viewed. If no IDs are provided, mark all alerts viewed."""
    store = get_default_document_store()
    targets = set(alert_ids or [])
    viewed_at = time.time()

    def mark(payload: Any) -> Any:
        if not isinstance(payload, dict):
            raise _AlertGone
        return {**payload, "viewed": True, "viewed_at": viewed_at}

    updated = 0
    for alert in list_alerts():
        if alert.viewed or (targets and alert.id not in targets):
            continue
        try:
            store.mutate_document(PROGENITOR_STATE_PREFIX, alert_key(alert.id), mark)
        except _AlertGone:
            continue
        updated += 1
    return updated


def clear_alerts() -> None:
    """Delete every alert document (tests)."""
    store = get_default_document_store()
    for alert in list_alerts():
        store.delete_document(PROGENITOR_STATE_PREFIX, alert_key(alert.id))
