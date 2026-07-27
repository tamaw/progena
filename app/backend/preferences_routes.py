"""User preferences API routes — the backend-persisted Settings/profile document.

Preferences (display name, timezone) live in the ``preferences`` state document
(see ``preferences_service.py``), the same document the agent edits as
``/config/preferences.json``. The Settings page renders from the realtime
``preferences.snapshot`` event, so this router exposes one GET (acknowledge +
snapshot) and one PUT (validate + persist + snapshot), mirroring the
``knowledge`` router's document-settings pattern.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.progenitor.state.preferences import validate_preferences_payload

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .preferences_service import load_preferences, save_preferences
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_preferences_snapshot

router = APIRouter(prefix="/api/ui/preferences", tags=["preferences"])


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def get_preferences(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    """Acknowledge a read and publish the snapshot on the UI stream."""
    request_id = _request_correlation_id(request, x_request_id)
    # load_preferences() does blocking document-store I/O (see save_preferences
    # below, which is threaded for the same reason) - broadcast_preferences_snapshot
    # would otherwise call it straight from this async handler and block the
    # event loop for the Postgres round-trip.
    snapshot = await asyncio.to_thread(load_preferences)
    broadcast_preferences_snapshot(snapshot, request_id=request_id)
    return acknowledgement_response(request_id, ["preferences.snapshot"])


@router.put("", status_code=202, response_model=UiRequestAcknowledgement)
async def replace_preferences(
    payload: dict[str, Any],
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
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
    return acknowledgement_response(request_id, ["preferences.snapshot"])
