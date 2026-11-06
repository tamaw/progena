"""Events routes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

import src.backend.alerts.routes as alerts_routes
import src.backend.canvases.events as canvases_events
import src.backend.chat.background_tasks as chat_background_tasks
import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.inbox.events as inbox_events
import src.backend.monitoring.status as monitoring_status
import src.backend.runs.events as runs_events
import src.backend.workflows.events as workflows_events
from src.backend.http import contracts as http_contracts
from src.backend.http.requests import request_correlation_id

logger = logging.getLogger(__name__)

router = APIRouter()


async def close_all_sse_connections():
    """
    Signal all SSE connections to close gracefully.

    Called during server shutdown to ensure SSE connections do not block
    the graceful shutdown process.
    """

    stream_count = len(events_stream._ui_stream_subscribers)
    logger.info("Closing SSE connections: %d stream", stream_count)

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = events_stream.get_sse_shutdown_event()
    shutdown_event.set()

    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}',
    }
    for queue in events_stream._ui_stream_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    monitoring_status.stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    tasks_to_cancel = [t for t in events_stream._active_ui_stream_tasks if not t.done()]
    if tasks_to_cancel:
        logger.info("Force-cancelling %d SSE tasks", len(tasks_to_cancel))
        for task in tasks_to_cancel:
            task.cancel()
        done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0)
        if pending:
            logger.warning("%d SSE tasks did not cancel in time", len(pending))

    events_stream._active_ui_stream_tasks.clear()

    logger.info("SSE shutdown signal sent to all connections")


async def _generate_ui_stream(
    last_event_id: str | None = None,
    client_id: str | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    """Generate the canonical typed UI event stream."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    current_task = asyncio.current_task()
    events_stream._ui_stream_subscribers[queue] = current_task
    client_id = client_id or uuid.uuid4().hex
    # A replacement connection owns a fresh interest set. Its predecessor's
    # cleanup sees this connection and therefore cannot remove these topics.
    events_stream.clear_topics(client_id)
    events_stream._ui_stream_clients[queue] = client_id
    workflows_events.routine_events.connections.add(client_id)
    if current_task:
        events_stream._active_ui_stream_tasks.add(current_task)

    shutdown_event = events_stream.get_sse_shutdown_event()
    monitoring_status.start_status_monitor()

    try:
        if shutdown_event.is_set():
            # A connection that arrives while the server drains is told to go away
            # without a `connection.ready`: a ready would make the browser resync
            # against a server that is about to close the stream.
            draining = events_stream._next_ui_envelope(
                events_contracts.UiEvent.CONNECTION_SHUTDOWN,
                {"reason": "server_shutdown", "message": "Server is shutting down"},
            )
            yield ServerSentEvent(
                event=events_contracts.SseEvent.UI_EVENT, data=draining.model_dump(), id=draining.id
            )
            return

        if last_event_id:
            try:
                replay_after = int(str(last_event_id).removeprefix("ui-") or 0)
            except ValueError:
                replay_after = 0
            for envelope in list(events_stream._ui_event_replay):
                if envelope.seq > replay_after:
                    yield ServerSentEvent(
                        event=events_contracts.SseEvent.UI_EVENT,
                        data=envelope.model_dump(),
                        id=envelope.id,
                    )

        connected = events_stream._next_ui_envelope(
            events_contracts.UiEvent.CONNECTION_READY,
            {
                "stream": "ui",
                "boot_id": events_stream.BOOT_ID,
                "alerts": alerts_routes._alerts_payload(),
            },
        )
        yield ServerSentEvent(
            event=events_contracts.SseEvent.UI_EVENT, data=connected.model_dump(), id=connected.id
        )
        await workflows_events.routine_events.lifecycle_snapshot(client_id)

        while True:
            if shutdown_event.is_set():
                envelope = events_stream._next_ui_envelope(
                    events_contracts.UiEvent.CONNECTION_SHUTDOWN,
                    {"reason": "server_shutdown", "message": "Server is shutting down"},
                )
                yield ServerSentEvent(
                    event=events_contracts.SseEvent.UI_EVENT,
                    data=envelope.model_dump(),
                    id=envelope.id,
                )
                break

            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=events_stream._SSE_HEARTBEAT_INTERVAL
                )
                if event.get("type") == "shutdown":
                    envelope = events_stream._next_ui_envelope(
                        events_contracts.UiEvent.CONNECTION_SHUTDOWN,
                        {"reason": "server_shutdown", "message": "Server is shutting down"},
                    )
                    yield ServerSentEvent(
                        event=events_contracts.SseEvent.UI_EVENT,
                        data=envelope.model_dump(),
                        id=envelope.id,
                    )
                    break
                envelope = event["envelope"]
                if (
                    "interest" in event
                    and event["interest"]
                    is not workflows_events.routine_events.interests.get(client_id)
                    and not runs_events.runs_events.has_interest(client_id, event["interest"])
                    and not inbox_events.inbox_message_events.has_interest(
                        client_id, event["interest"]
                    )
                ):
                    continue
                yield ServerSentEvent(
                    event=events_contracts.SseEvent.UI_EVENT,
                    data=envelope.model_dump(),
                    id=envelope.id,
                )
            except TimeoutError:
                envelope = events_stream._next_ui_envelope(
                    events_contracts.UiEvent.CONNECTION_HEARTBEAT, {"timestamp": time.time()}
                )
                yield ServerSentEvent(
                    event=events_contracts.SseEvent.UI_EVENT,
                    data=envelope.model_dump(),
                    id=envelope.id,
                )
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)
        # A reconnect may already have replaced this socket.
        if client_id not in events_stream._ui_stream_clients.values():
            workflows_events.routine_events.disconnected(client_id)
            canvases_events.canvas_events.disconnected(client_id)
            runs_events.runs_events.disconnected(client_id)
            chat_background_tasks.background_task_events.unsubscribe(client_id)
            inbox_events.inbox_message_events.disconnected(client_id)
            events_stream.clear_topics(client_id)
        if current_task:
            events_stream._active_ui_stream_tasks.discard(current_task)
        if not events_stream._ui_stream_subscribers:
            monitoring_status.stop_status_monitor()


@router.get("/stream", response_class=EventSourceResponse)
async def ui_stream(
    response: Response,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after: str | None = Query(default=None, max_length=64),
    client_id: str | None = Query(default=None, max_length=64),
) -> AsyncGenerator[ServerSentEvent, None]:
    """Canonical typed SSE stream for all frontend UI updates.

    ``after`` carries the replay cursor for a reconnect that cannot send
    ``Last-Event-ID`` (a newly constructed browser EventSource); the header wins.
    """
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_ui_stream(last_event_id or after, client_id):
        yield event


class StreamTopicRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    topic: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")
    watching: bool


_MAX_TOPICS_PER_CLIENT = 32


@router.put(
    "/stream/topics", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def put_stream_topic(
    body: StreamTopicRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Register or release this tab's interest in a topic-scoped event stream.

    Topic events reach only tabs that watch the topic. The interest ends with the
    stream connection, so a reconnecting page registers it again and reloads.
    """
    if body.client_id not in events_stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="Topic requests require an active UI stream")
    request_id = request_correlation_id(request, x_request_id)
    if body.watching:
        held = events_stream._ui_stream_topics.get(body.client_id, set())
        if body.topic not in held and len(held) >= _MAX_TOPICS_PER_CLIENT:
            raise HTTPException(status_code=429, detail="Too many watched topics")
        events_stream.subscribe_topic(body.client_id, body.topic)
    else:
        events_stream.unsubscribe_topic(body.client_id, body.topic)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.STREAM_TOPIC_UPDATED,
        {"topic": body.topic, "watching": body.watching},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.STREAM_TOPIC_UPDATED]
    )


@router.get("/status", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_status(
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Reply to the asking tab with the full runtime status the Dashboard shows."""
    if client_id not in events_stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="Status requests require an active UI stream")
    request_id = request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.STATUS_UPDATED,
        await monitoring_status._get_current_status(),
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.STATUS_UPDATED]
    )


class StreamResolveRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    request_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=64
    )


class StreamResolveResponse(BaseModel):
    boot_id: str
    replayed: list[str]
    unknown: list[str]
    lost: list[str]


@router.post("/stream/resolve", response_model=StreamResolveResponse)
async def resolve_stream_requests(body: StreamResolveRequest) -> StreamResolveResponse:
    """Re-deliver the replies to requests whose stream dropped before they arrived.

    A reply goes only to the tab's own stream, and the tab's pending requests then
    settle through the normal event path. ``unknown`` ids have no recorded reply
    (still being worked on, or older than the record), ``lost`` ids were answered
    with something too large to keep. Nothing is consumed.
    """
    if body.client_id not in events_stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="Resolving requests needs an active UI stream")
    replayed, unknown, lost = events_stream.replay_request_outcomes(
        body.client_id, body.request_ids
    )
    return StreamResolveResponse(
        boot_id=events_stream.BOOT_ID, replayed=replayed, unknown=unknown, lost=lost
    )
