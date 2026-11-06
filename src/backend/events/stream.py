"""Events stream."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
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

# Identifies this server process. `connection.ready` carries it, so a browser that
# sees it change knows the server restarted and the replies this process had
# recorded (below) are gone.
BOOT_ID = uuid.uuid4().hex
_OUTCOME_TTL_SECONDS = 120.0
_OUTCOME_BUDGET_BYTES = 8 * 1024 * 1024
_OUTCOME_MAX_ENVELOPE_BYTES = 512 * 1024
_OUTCOMES_PER_REQUEST = 8
_ui_stream_topics: dict[str, set[str]] = {}

# Events every open tab renders as app chrome: alerts, the status bar, and the
# nav indicators (inbox badge, per-route activity dot, chat busy/unread dot).
# Anything else must be a reply to one tab (``client_id``) or fan out only to the
# tabs watching a ``topic``.
GLOBAL_UI_EVENTS: frozenset[str] = frozenset(
    {
        events_contracts.UiEvent.ALERT,
        events_contracts.UiEvent.ALERTS_UPDATED,
        events_contracts.UiEvent.CHAT_RESPONSE_ACTIVITY,
        events_contracts.UiEvent.CHAT_RESPONSE_CANCELLED,
        events_contracts.UiEvent.CHAT_RESPONSE_COMPLETED,
        events_contracts.UiEvent.CHAT_RESPONSE_FAILED,
        events_contracts.UiEvent.CHAT_RESPONSE_STARTED,
        events_contracts.UiEvent.CONNECTION_HEARTBEAT,
        events_contracts.UiEvent.CONNECTION_READY,
        events_contracts.UiEvent.CONNECTION_SHUTDOWN,
        events_contracts.UiEvent.INBOX_COUNTS_UPDATED,
        events_contracts.UiEvent.PREFERENCES_SNAPSHOT,
        events_contracts.UiEvent.PROFILES_SNAPSHOT,
        events_contracts.UiEvent.NAV_ACTIVITY,
        events_contracts.UiEvent.PREFERENCES_SNAPSHOT,
        events_contracts.UiEvent.PROFILES_SNAPSHOT,
    }
)

# Domain events that light a nav route's "new activity" dot in tabs that are not
# on that route. A domain event addressed to a topic reaches only tabs watching
# it, so any such event that is not a reply to one tab also emits a global
# ``nav.activity`` carrying just the route.
NAV_ACTIVITY_ROUTES: dict[str, str] = {
    events_contracts.UiEvent.AGENTS_SNAPSHOT: "/agents",
    events_contracts.UiEvent.CANVASES_SNAPSHOT: "/canvases",
    events_contracts.UiEvent.INSTRUCTIONS_SNAPSHOT: "/instructions",
    events_contracts.UiEvent.TASKS_SNAPSHOT: "/tasks",
    events_contracts.UiEvent.KNOWLEDGE_SNAPSHOT: "/knowledge",
    events_contracts.UiEvent.MCP_SNAPSHOT: "/tools",
    events_contracts.UiEvent.PLUGINS_SNAPSHOT: "/plugins",
    events_contracts.UiEvent.PROFILES_SNAPSHOT: "/profiles",
    events_contracts.UiEvent.REPOSITORIES_SNAPSHOT: "/repositories",
    events_contracts.UiEvent.RUN_DELETED: "/runs",
    events_contracts.UiEvent.RUN_UPDATED: "/runs",
    events_contracts.UiEvent.SCHEDULES_SNAPSHOT: "/schedules",
    events_contracts.UiEvent.TASK_ACTIVITY_UPDATED: "/tasks",
    events_contracts.UiEvent.TASK_CONTENT_UPDATED: "/tasks",
    events_contracts.UiEvent.WIDGETS_UPDATED: "/dashboard",
    events_contracts.UiEvent.WORKFLOW_DELETED: "/workflows",
    events_contracts.UiEvent.WORKFLOW_RUN_COMPLETED: "/workflows",
    events_contracts.UiEvent.WORKFLOW_RUN_FAILED: "/workflows",
    events_contracts.UiEvent.WORKFLOW_RUN_STARTED: "/workflows",
    events_contracts.UiEvent.WORKFLOW_RUN_UPDATED: "/workflows",
    events_contracts.UiEvent.WORKFLOW_RUNS_SNAPSHOT: "/workflows",
    events_contracts.UiEvent.WORKFLOW_UPDATED: "/workflows",
    events_contracts.UiEvent.WORKFLOWS_SNAPSHOT: "/workflows",
    "agent.routine.run.started": "/routines",
    "agent.routine.run.completed": "/routines",
    "agent.routine.run.failed": "/routines",
}


def subscribe_topic(client_id: str, topic: str) -> None:
    """Register one tab's interest in a topic; idempotent."""
    _ui_stream_topics.setdefault(client_id, set()).add(topic)


def unsubscribe_topic(client_id: str, topic: str) -> None:
    topics = _ui_stream_topics.get(client_id)
    if topics is None:
        return
    topics.discard(topic)
    if not topics:
        del _ui_stream_topics[client_id]


def clear_topics(client_id: str) -> None:
    _ui_stream_topics.pop(client_id, None)


def has_topic(client_id: str, topic: str) -> bool:
    return topic in _ui_stream_topics.get(client_id, ())


def has_topic_watchers(topic: str) -> bool:
    """True when at least one connected tab watches ``topic``."""
    return any(topic in topics for topics in _ui_stream_topics.values())


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


@dataclass
class _RequestOutcome:
    recorded_at: float
    client_id: str | None
    envelopes: list[events_contracts.UiEventEnvelope]
    size: int
    too_large: bool = False


_request_outcomes: OrderedDict[str, _RequestOutcome] = OrderedDict()
_request_outcome_bytes = 0


def _purge_request_outcomes() -> None:
    global _request_outcome_bytes
    cutoff = time.monotonic() - _OUTCOME_TTL_SECONDS
    while _request_outcomes:
        oldest_id, oldest = next(iter(_request_outcomes.items()))
        if oldest.recorded_at >= cutoff and _request_outcome_bytes <= _OUTCOME_BUDGET_BYTES:
            break
        del _request_outcomes[oldest_id]
        _request_outcome_bytes -= oldest.size


def _retain_request_outcome(
    request_id: str, client_id: str | None, envelope: events_contracts.UiEventEnvelope
) -> None:
    """Keep the reply to a request so its tab can ask for it again after a reconnect.

    A reply sent while the tab's stream was down (or to a queue that closed with
    it) is otherwise gone: replies are not in the global replay buffer.  The
    record is bounded by age and total size; a reply too large to keep is noted
    as such so the tab is told it cannot be recovered instead of waiting.
    """
    global _request_outcome_bytes
    _purge_request_outcomes()
    outcome = _request_outcomes.get(request_id)
    if outcome is None:
        outcome = _RequestOutcome(time.monotonic(), client_id, [], 0)
        _request_outcomes[request_id] = outcome
    elif outcome.client_id != client_id:
        # A request is answered to one audience; anything else is a different
        # request reusing the id, which must not widen who can read the reply.
        return
    if outcome.too_large or len(outcome.envelopes) >= _OUTCOMES_PER_REQUEST:
        return
    size = len(envelope.model_dump_json())
    if size > _OUTCOME_MAX_ENVELOPE_BYTES:
        _request_outcome_bytes -= outcome.size
        outcome.envelopes, outcome.size, outcome.too_large = [], 0, True
        return
    outcome.envelopes.append(envelope)
    outcome.size += size
    _request_outcome_bytes += size
    _purge_request_outcomes()


def replay_request_outcomes(
    client_id: str, request_ids: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Queue the recorded replies for *request_ids* onto *client_id*'s stream.

    Returns ``(replayed, unknown, lost)``: ids whose replies were queued, ids with
    no record (not answered yet, or older than the record), and ids that were
    answered but are too large to have been kept.  Replies recorded for another
    tab are never replayed here.  Nothing is consumed, so a tab that drops again
    can ask again.
    """
    _purge_request_outcomes()
    queues = [q for q in _ui_stream_subscribers if _ui_stream_clients.get(q) == client_id]
    replayed: list[str] = []
    unknown: list[str] = []
    lost: list[str] = []
    for request_id in request_ids:
        outcome = _request_outcomes.get(request_id)
        if outcome is None or outcome.client_id not in (None, client_id):
            unknown.append(request_id)
        elif outcome.too_large:
            lost.append(request_id)
        else:
            delivered = bool(queues)
            for queue in queues:
                for envelope in outcome.envelopes:
                    try:
                        queue.put_nowait(
                            {"type": events_contracts.SseEvent.UI_EVENT, "envelope": envelope}
                        )
                    except asyncio.QueueFull:
                        delivered = False
            (replayed if delivered else unknown).append(request_id)
    return replayed, unknown, lost


def broadcast_ui_event(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    scope: Literal["global", "session", "chat"] = "global",
    session_id: str | None = None,
    request_id: str | None = None,
    client_id: str | None = None,
    interest: object | None = None,
    topic: str | None = None,
    exclude_client_id: str | None = None,
) -> events_contracts.UiEventEnvelope:
    """Broadcast one typed event envelope to canonical /api/ui/stream subscribers.

    When *request_id* is provided it is embedded in the envelope payload so the
    frontend can correlate the event to a pending operation.

    Audience is exactly one of: *client_id* (reply to one tab), *topic* (tabs
    that called :func:`subscribe_topic`), or neither (every tab; reserved for
    :data:`GLOBAL_UI_EVENTS`).  A topic fan-out can exclude its requester after
    that tab has received a separate request-correlated reply.
    """
    if client_id is not None and topic is not None:
        raise ValueError("broadcast_ui_event takes client_id or topic, not both")
    if exclude_client_id is not None and topic is None:
        raise ValueError("exclude_client_id requires a topic audience")
    if request_id is not None:
        payload = {**payload, "request_id": request_id}
    envelope = _next_ui_envelope(event_type, payload, scope=scope, session_id=session_id)
    # Targeted detail is recovered by a subscription snapshot, never replayed
    # to other connections or to a page that has stopped watching.
    if client_id is None and topic is None:
        _ui_event_replay.append(envelope)
        del _ui_event_replay[:-_UI_EVENT_REPLAY_LIMIT]

    event = {"type": events_contracts.SseEvent.UI_EVENT, "envelope": envelope}
    if client_id and interest is not None:
        event["interest"] = interest
    dead: list[asyncio.Queue] = []
    for queue in _ui_stream_subscribers:
        subscriber = _ui_stream_clients.get(queue)
        if client_id and subscriber != client_id:
            continue
        if topic is not None and (subscriber is None or not has_topic(subscriber, topic)):
            continue
        if exclude_client_id is not None and subscriber == exclude_client_id:
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
    route = NAV_ACTIVITY_ROUTES.get(event_type)
    if route is not None and client_id is None:
        broadcast_ui_event(events_contracts.UiEvent.NAV_ACTIVITY, {"route": route})
    if request_id is not None:
        _retain_request_outcome(request_id, client_id, envelope)
    return envelope
