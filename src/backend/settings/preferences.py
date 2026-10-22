"""User preferences API routes — the backend-persisted Settings/profile document.

Preferences (display name, timezone) live in the ``preferences`` state document
(see ``settings/preferences_store.py``), the same document the agent edits as
``/config/preferences.json``. The Settings page renders from the realtime
``preferences.snapshot`` event, so this router exposes one GET (acknowledge +
snapshot) and one PUT (validate + persist + snapshot), mirroring the
``knowledge`` router's document-settings pattern.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.settings.preferences_store as settings_preferences_store
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.settings.preferences_store import load_preferences, save_preferences
from src.progena.settings.preferences import validate_preferences_payload

router = APIRouter(prefix="/api/ui/preferences", tags=["preferences"])


@router.get("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_preferences(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Acknowledge a read and publish the snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    # load_preferences() does blocking document-store I/O (see save_preferences
    # below, which is threaded for the same reason) - broadcast_preferences_snapshot
    # would otherwise call it straight from this async handler and block the
    # event loop for the Postgres round-trip.
    snapshot = await asyncio.to_thread(load_preferences)
    broadcast_preferences_snapshot(snapshot, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["preferences.snapshot"])


@router.put("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def replace_preferences(
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> http_contracts.UiRequestAcknowledgement:
    """Validate and persist the full preferences document.

    Shares ``validate_preferences_payload`` with the agent's
    ``/config/preferences.json`` write path, so both can never disagree on shape.
    """
    request_id = _request_correlation_id(request, x_request_id)
    try:
        normalized = validate_preferences_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = await asyncio.to_thread(save_preferences, normalized)
    broadcast_preferences_snapshot(saved, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["preferences.snapshot"])


def broadcast_preferences_snapshot(
    preferences: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current user preferences to /api/ui/stream subscribers.

    Emitted after backend-side preference mutations and whenever the
    ``preferences`` state document changes (surfaced via the Postgres NOTIFY
    watcher), so the Settings page reflects changes live without refetching.
    Callers that already built the payload can pass it in to avoid a second
    document read and keep the event consistent with the HTTP response.
    """
    payload = settings_preferences_store.load_preferences() if preferences is None else preferences
    return events_stream.broadcast_ui_event("preferences.snapshot", payload, request_id=request_id)
