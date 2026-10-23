"""Events stream."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any, Literal

import src.backend.events.contracts as events_contracts

logger = logging.getLogger(__name__)


def _parse_heartbeat_interval(raw: str, default: float = 15.0) -> float:
    try:
        val = float(raw)
        if val <= 0:
            raise ValueError("must be positive")
        return val
    except ValueError:
        logger.warning(
            "Invalid SSE_HEARTBEAT_INTERVAL %r — must be a positive number; using %.0fs",
            raw,
            default,
        )
        return default


_SSE_HEARTBEAT_INTERVAL: float = _parse_heartbeat_interval(
    os.getenv("SSE_HEARTBEAT_INTERVAL", "15")
)
_ui_stream_subscribers: dict[asyncio.Queue, asyncio.Task | None] = {}
_ui_stream_clients: dict[asyncio.Queue, str] = {}
_ui_event_seq = 0
_ui_event_replay: list[events_contracts.UiEventEnvelope] = []
_UI_EVENT_REPLAY_LIMIT = 200
_sse_shutdown_event: asyncio.Event | None = None
_active_ui_stream_tasks: set[asyncio.Task] = set()


def has_active_ui_stream_subscribers() -> bool:
    """Check if there are any active canonical UI stream subscribers.

    Telegram fallback should follow the modern ``/api/ui/stream`` connection,
    not the legacy ``/api/ui/events`` subscriber list.
    """
    return len(_ui_stream_subscribers) > 0


def get_subscriber_counts() -> dict[str, int]:
    """Get count of active canonical stream subscribers."""
    n = len(_ui_stream_subscribers)
    return {"stream": n, "web": n, "total": n}


def get_sse_shutdown_event() -> asyncio.Event:
    """Get or create the SSE shutdown event."""
    global _sse_shutdown_event
    if _sse_shutdown_event is None:
        _sse_shutdown_event = asyncio.Event()
    return _sse_shutdown_event


def _next_ui_envelope(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    scope: Literal["global", "session", "chat"] = "global",
    session_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    global _ui_event_seq
    _ui_event_seq += 1
    return events_contracts.UiEventEnvelope(
        id=f"ui-{_ui_event_seq}",
        seq=_ui_event_seq,
        type=event_type,
        scope=scope,
        session_id=session_id,
        payload=dict(payload),
    )


def broadcast_ui_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    scope: Literal["global", "session", "chat"] = "global",
    session_id: str | None = None,
    request_id: str | None = None,
    client_id: str | None = None,
    interest: object | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast one typed event envelope to canonical /api/ui/stream subscribers.

    When *request_id* is provided it is embedded in the envelope payload so the
    frontend can correlate the event to a pending operation.
    """
    if request_id is not None:
        payload = {**payload, "request_id": request_id}
    envelope = _next_ui_envelope(event_type, payload, scope=scope, session_id=session_id)
    # Targeted detail is recovered by a subscription snapshot, never replayed
    # to other connections or to a page that has stopped watching.
    if client_id is None:
        _ui_event_replay.append(envelope)
        del _ui_event_replay[:-_UI_EVENT_REPLAY_LIMIT]

    event = {"type": "ui.event", "envelope": envelope}
    if client_id and interest is not None:
        event["interest"] = interest
    dead: list[asyncio.Queue] = []
    for queue in _ui_stream_subscribers:
        if client_id and _ui_stream_clients.get(queue) != client_id:
            continue
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(queue)
    for q in dead:
        task = _ui_stream_subscribers.pop(q, None)
        if task is not None and not task.done():
            # A full queue means the handler stopped draining — it is blocked
            # writing to a dead or half-open socket. Cancelling it closes the
            # HTTP connection, so a live client's EventSource reconnects
            # instead of staying open-but-deaf, and true zombies get reaped.
            task.cancel()
        logger.warning("Event queue full for UI stream subscriber — closing stale connection")
    return envelope
