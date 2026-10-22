"""Events routes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Header, Query
from fastapi.responses import Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

import app.backend.alerts.routes as alerts_routes
import app.backend.events.stream as events_stream
import app.backend.modules.events as modules_events
import app.backend.monitoring.status as monitoring_status
import app.backend.workflows.events as workflows_events

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
    events_stream._ui_stream_clients[queue] = client_id
    workflows_events.routine_events.connections.add(client_id)
    if current_task:
        events_stream._active_ui_stream_tasks.add(current_task)

    shutdown_event = events_stream.get_sse_shutdown_event()
    monitoring_status.start_status_monitor()

    try:
        if last_event_id:
            try:
                replay_after = int(str(last_event_id).removeprefix("ui-") or 0)
            except ValueError:
                replay_after = 0
            for envelope in list(events_stream._ui_event_replay):
                if envelope.seq > replay_after:
                    yield ServerSentEvent(
                        event="ui.event",
                        data=envelope.model_dump(),
                        id=envelope.id,
                    )

        initial_status = await monitoring_status._get_current_status()
        connected = events_stream._next_ui_envelope(
            "connection.ready",
            {"stream": "ui", "status": initial_status, "alerts": alerts_routes._alerts_payload()},
        )
        yield ServerSentEvent(event="ui.event", data=connected.model_dump(), id=connected.id)
        await workflows_events.routine_events.lifecycle_snapshot(client_id)

        while True:
            if shutdown_event.is_set():
                envelope = events_stream._next_ui_envelope(
                    "connection.shutdown",
                    {"reason": "server_shutdown", "message": "Server is shutting down"},
                )
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
                break

            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=events_stream._SSE_HEARTBEAT_INTERVAL
                )
                if event.get("type") == "shutdown":
                    envelope = events_stream._next_ui_envelope(
                        "connection.shutdown",
                        {"reason": "server_shutdown", "message": "Server is shutting down"},
                    )
                    yield ServerSentEvent(
                        event="ui.event", data=envelope.model_dump(), id=envelope.id
                    )
                    break
                envelope = event["envelope"]
                if "interest" in event and event[
                    "interest"
                ] is not workflows_events.routine_events.interests.get(client_id):
                    continue
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
            except TimeoutError:
                envelope = events_stream._next_ui_envelope(
                    "connection.heartbeat", {"timestamp": time.time()}
                )
                yield ServerSentEvent(event="ui.event", data=envelope.model_dump(), id=envelope.id)
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)
        # A reconnect may already have replaced this socket.
        if client_id not in events_stream._ui_stream_clients.values():
            workflows_events.routine_events.disconnected(client_id)
            modules_events.module_events.disconnected(client_id)
        if current_task:
            events_stream._active_ui_stream_tasks.discard(current_task)
        if not events_stream._ui_stream_subscribers:
            monitoring_status.stop_status_monitor()


@router.get("/stream", response_class=EventSourceResponse)
async def ui_stream(
    response: Response,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    client_id: str | None = Query(default=None, max_length=64),
) -> AsyncGenerator[ServerSentEvent, None]:
    """Canonical typed SSE stream for all frontend UI updates."""
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    async for event in _generate_ui_stream(last_event_id, client_id):
        yield event
