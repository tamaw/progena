"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])


class UIStatusResponse(BaseModel):
    """Combined status response for the UI."""
    connected: bool
    server_status: str
    progenitor_running: bool
    uptime_seconds: float
    version: str
    memory_mb: Optional[float] = None
    active_requests: int
    rate_limit_remaining_minute: int
    rate_limit_remaining_hour: int


class SubAgentInfo(BaseModel):
    """Sub-agent information."""
    name: str
    status: str
    task: Optional[str] = None
    progress: Optional[int] = None


class SubAgentsResponse(BaseModel):
    """Response containing sub-agent status."""
    agents: list[SubAgentInfo]
    total: int


class TaskInfo(BaseModel):
    """Active task information."""
    id: str
    title: str
    status: str
    description: Optional[str] = None
    progress: Optional[int] = None


class TasksResponse(BaseModel):
    """Response containing active tasks."""
    tasks: list[TaskInfo]
    total: int


class AlertInfo(BaseModel):
    """Alert information."""
    id: str
    type: str
    title: str
    message: str
    timestamp: float
    urgent: bool = False
    requires_response: bool = False


class AlertsResponse(BaseModel):
    """Response containing pending alerts."""
    alerts: list[AlertInfo]
    total: int


# In-memory alert storage (would be replaced with proper storage in production)
_pending_alerts: list[AlertInfo] = []
_alert_id_counter = 0


def generate_alert_id() -> str:
    """Generate a unique alert ID."""
    global _alert_id_counter
    _alert_id_counter += 1
    return f"alert-{int(time.time())}-{_alert_id_counter}"


@router.get("/status", response_model=UIStatusResponse)
async def get_ui_status():
    """
    Get combined status information for the UI.

    Aggregates health, rate limit, and progenitor status into a single response.
    """
    from . import main as chat_main

    try:
        # Get basic health
        health = await chat_main.health_check_detailed()

        # Build response
        return UIStatusResponse(
            connected=True,
            server_status=health.get("status", "unknown"),
            progenitor_running=health.get("progenitor", {}).get("running", False),
            uptime_seconds=health.get("uptime_seconds", 0),
            version=health.get("version", "unknown"),
            memory_mb=health.get("memory", {}).get("rss_mb"),
            active_requests=health.get("shutdown", {}).get("active_requests", 0),
            rate_limit_remaining_minute=health.get("rate_limiter", {}).get("requests_per_minute_limit", 60),
            rate_limit_remaining_hour=health.get("rate_limiter", {}).get("requests_per_hour_limit", 500),
        )
    except Exception as e:
        logger.error("Failed to get UI status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents", response_model=SubAgentsResponse)
async def get_sub_agents():
    """
    Get status of all sub-agents.

    Returns information about active sub-agents and their current tasks.
    """
    from . import main as chat_main

    # Get health to check if progenitor is running
    try:
        health = await chat_main.health_check_detailed()
        progenitor_running = health.get("progenitor", {}).get("running", False)
    except Exception:
        progenitor_running = False

    # In a full implementation, this would query the progenitor for sub-agent status
    # For now, return simulated data based on whether progenitor is running
    if progenitor_running:
        agents = [
            SubAgentInfo(
                name="Planner",
                status="active",
                task="Monitoring task queue",
                progress=None
            ),
            SubAgentInfo(
                name="Executor",
                status="idle",
                task="Awaiting tasks",
                progress=None
            ),
            SubAgentInfo(
                name="Monitor",
                status="active",
                task="Watching system health",
                progress=None
            ),
        ]
    else:
        agents = []

    return SubAgentsResponse(agents=agents, total=len(agents))


@router.get("/tasks", response_model=TasksResponse)
async def get_active_tasks():
    """
    Get list of active tasks.

    Returns information about tasks currently being processed.
    """
    from . import main as chat_main

    try:
        health = await chat_main.health_check_detailed()
        active_requests = health.get("shutdown", {}).get("active_requests", 0)
    except Exception:
        active_requests = 0

    # In a full implementation, this would query the progenitor for task status
    tasks = []

    if active_requests > 0:
        tasks.append(TaskInfo(
            id="task-active-1",
            title="Processing Chat Request",
            status="running",
            description=f"Handling {active_requests} active request(s)",
            progress=None
        ))

    return TasksResponse(tasks=tasks, total=len(tasks))


@router.get("/alerts", response_model=AlertsResponse)
async def get_alerts():
    """
    Get pending alerts that require user attention.

    Returns alerts generated by the progenitor agent.
    """
    return AlertsResponse(alerts=_pending_alerts, total=len(_pending_alerts))


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str):
    """
    Dismiss an alert.

    Removes the alert from the pending list.
    """
    global _pending_alerts

    original_count = len(_pending_alerts)
    _pending_alerts = [a for a in _pending_alerts if a.id != alert_id]

    if len(_pending_alerts) == original_count:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {"status": "dismissed", "alert_id": alert_id}


@router.post("/alerts/{alert_id}/respond")
async def respond_to_alert(alert_id: str, response: str = "acknowledged"):
    """
    Respond to an alert.

    Acknowledges the alert and optionally sends a response back to the agent.
    """
    global _pending_alerts

    alert = next((a for a in _pending_alerts if a.id == alert_id), None)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Remove from pending
    _pending_alerts = [a for a in _pending_alerts if a.id != alert_id]

    # In a full implementation, this would send the response to the progenitor
    logger.info("Alert %s responded with: %s", alert_id, response)

    return {"status": "responded", "alert_id": alert_id, "response": response}


@router.get("/prompts")
async def get_prompts():
    """
    Get available prompt templates.

    Returns list of prompt templates from data/prompts/ directory.
    """
    # TODO: Implement actual prompt loading from data/prompts/
    return {"prompts": []}


@router.get("/logs")
async def get_logs():
    """
    Get agent logs.

    Returns recent logs from the agent.
    """
    # TODO: Implement actual log reading from logs/
    return {"logs": ""}


def create_alert(
    type: str,
    title: str,
    message: str,
    urgent: bool = False,
    requires_response: bool = False
) -> AlertInfo:
    """
    Create a new alert (called by the progenitor agent).

    Args:
        type: Alert type (info, warning, error)
        title: Alert title
        message: Alert message
        urgent: Whether the alert is urgent
        requires_response: Whether the alert requires a user response

    Returns:
        Created AlertInfo
    """
    alert = AlertInfo(
        id=generate_alert_id(),
        type=type,
        title=title,
        message=message,
        timestamp=time.time(),
        urgent=urgent,
        requires_response=requires_response
    )
    _pending_alerts.append(alert)

    # Keep only last 100 alerts
    while len(_pending_alerts) > 100:
        _pending_alerts.pop(0)

    return alert


# Event subscribers for SSE
_event_subscribers: list[asyncio.Queue] = []

# Status monitor state
_status_monitor_task: Optional[asyncio.Task] = None
_last_status: dict = {}


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""
    from . import main as chat_main
    import psutil

    now = time.time()
    start_time = getattr(chat_main, '_server_start_time', now)
    client = getattr(chat_main, 'client', None)
    shutdown_mgr = getattr(chat_main, 'shutdown_manager', None)

    # Try to ensure client is connected (non-blocking check)
    progenitor_running = False
    if client:
        # If client hasn't started yet, try to start it (connects to IPC)
        if not client._started and hasattr(client, 'start'):
            try:
                await asyncio.wait_for(client.start(), timeout=2.0)
            except Exception as e:
                logger.debug("Failed to start client for status check: %s", e)

        progenitor_running = client.is_running if hasattr(client, 'is_running') else False

    status = {
        "status": "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": progenitor_running,
        "version": getattr(chat_main.config, 'api_version', '0.1.0'),
    }

    if shutdown_mgr and shutdown_mgr.is_shutting_down:
        status["status"] = "draining"

    # Memory info
    try:
        process = psutil.Process()
        status["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        status["memory_mb"] = None

    # Progenitor details
    if client and hasattr(client, 'get_status'):
        try:
            status["progenitor_details"] = client.get_status()
        except Exception:
            pass

    return status


async def _status_monitor():
    """Background task that polls status and broadcasts to SSE subscribers.

    Pushes status updates every 10 seconds.
    """
    global _last_status

    update_interval = 10  # Push status every 10 seconds

    print("[StatusMonitor] Starting status monitor loop", flush=True)

    while True:
        try:
            await asyncio.sleep(update_interval)

            subscriber_count = len(_event_subscribers)
            print(f"[StatusMonitor] Check: {subscriber_count} subscribers", flush=True)

            if subscriber_count == 0:
                continue  # No subscribers, skip

            current = await _get_current_status()
            _last_status = current.copy()
            broadcast_event("status", current)
            print(f"[StatusMonitor] Pushed status (uptime: {current.get('uptime_seconds', 0):.1f}s)", flush=True)

        except asyncio.CancelledError:
            print("[StatusMonitor] Cancelled", flush=True)
            break
        except Exception as e:
            print(f"[StatusMonitor] Error: {e}", flush=True)
            await asyncio.sleep(10)


def _status_changed(current: dict, previous: dict) -> bool:
    """Check if status has meaningfully changed."""
    if not previous:
        return True

    # Check key fields for changes
    for key in ("status", "progenitor_running"):
        if current.get(key) != previous.get(key):
            return True

    # Check memory change > 10MB
    curr_mem = current.get("memory_mb") or 0
    prev_mem = previous.get("memory_mb") or 0
    if abs(curr_mem - prev_mem) > 10:
        return True

    return False


def start_status_monitor():
    """Start the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task is None or _status_monitor_task.done():
        _status_monitor_task = asyncio.create_task(_status_monitor())
        print("[StatusMonitor] Started background task", flush=True)


def stop_status_monitor():
    """Stop the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task and not _status_monitor_task.done():
        _status_monitor_task.cancel()
        logger.info("Status monitor stopped")


async def _generate_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for a subscriber."""
    import json

    queue: asyncio.Queue = asyncio.Queue()
    _event_subscribers.append(queue)

    # Start status monitor if not running
    start_status_monitor()

    try:
        # Send initial connection event with current status
        initial_status = await _get_current_status()
        yield f"event: connected\ndata: {json.dumps(initial_status)}\n\n"

        while True:
            try:
                # Wait for event with timeout (30s keepalive)
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except asyncio.TimeoutError:
                # Send heartbeat with current status (keeps connection alive)
                heartbeat_status = await _get_current_status()
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat_status)}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        if queue in _event_subscribers:
            _event_subscribers.remove(queue)


@router.get("/events")
async def event_stream():
    """
    Server-Sent Events endpoint for real-time updates.

    Connect to receive real-time notifications about:
    - Status changes
    - New alerts
    - Task updates
    - Sub-agent activity
    """
    return StreamingResponse(
        _generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


def broadcast_event(event_type: str, data: dict):
    """
    Broadcast an event to all SSE subscribers.

    Args:
        event_type: Type of event (alert, status, task, agent)
        data: Event data as JSON-serializable dict
    """
    import json

    event = {
        "type": event_type,
        "data": json.dumps(data)
    }

    for queue in _event_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for subscriber")


def broadcast_alert(alert: AlertInfo):
    """Broadcast a new alert to all subscribers."""
    broadcast_event("alert", {
        "id": alert.id,
        "type": alert.type,
        "title": alert.title,
        "message": alert.message,
        "urgent": alert.urgent
    })


def broadcast_status_change(status: str, details: dict = None):
    """Broadcast a status change to all subscribers."""
    broadcast_event("status", {
        "status": status,
        "details": details or {},
        "timestamp": time.time()
    })
