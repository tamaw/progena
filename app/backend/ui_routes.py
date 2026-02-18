"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.

Reads real state data from data/state/ directory:
- health.json: Agent health status
- registry.json: Registered agents and tools
- build_state.json: Agent builds ONLY (inner loop, LLM messages, verification)
- current_state.json: Progenitor agent state (chat history, tool calls) - user session
- schedule.json: Scheduled tasks (system feature, persists beyond user sessions)
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .state_reader import get_state_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])


class BuildInfoResponse(BaseModel):
    """Build status information."""
    in_progress: bool = False
    phase: str | None = None  # observe, think, plan, build, execute, verify, learn
    task_description: str | None = None
    started_at: str | None = None
    iteration: int = 0
    score: float = 0.0
    message_count: int = 0


class UIStatusResponse(BaseModel):
    """Combined status response for the UI."""
    connected: bool
    server_status: str
    progenitor_running: bool
    progenitor_status: str = "unknown"  # Raw status from health.json
    uptime_seconds: float
    version: str
    memory_mb: float | None = None
    active_requests: int
    rate_limit_minute_limit: int
    rate_limit_hour_limit: int
    # State data
    cycle_count: int = 0
    run_id: str | None = None
    messages_count: int = 0
    pid: int | None = None
    last_active: str | None = None
    # Build status
    build: BuildInfoResponse | None = None
    # Lists
    loops: list["LoopInfo"] = []
    agents: list["SubAgentInfo"] = []
    backlog: list["BacklogItemInfo"] = []
    schedule: list["ScheduleItemInfo"] = []


class LoopInfo(BaseModel):
    """Processing loop information (Planner/Executor)."""
    name: str
    status: str
    current_task: str | None = None


class LoopsResponse(BaseModel):
    """Response containing loop status."""
    loops: list[LoopInfo]


class SubAgentInfo(BaseModel):
    """Sub-agent (registered agent) information."""
    name: str
    status: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    transport: str = "stdio"
    task: str | None = None
    progress: int | None = None


class SubAgentsResponse(BaseModel):
    """Response containing registered agents."""
    agents: list[SubAgentInfo]
    total: int


class BacklogItemInfo(BaseModel):
    """Backlog item information."""
    id: str
    type: str
    status: str
    description: str


class BacklogResponse(BaseModel):
    """Response containing backlog items."""
    items: list[BacklogItemInfo]
    total: int


class ScheduleItemInfo(BaseModel):
    """Schedule item information."""
    id: str
    name: str
    schedule: str
    next_run: str | None
    enabled: bool


class ScheduleResponse(BaseModel):
    """Response containing schedule items."""
    items: list[ScheduleItemInfo]
    total: int


class TaskInfo(BaseModel):
    """Active task information."""
    id: str
    title: str
    status: str
    description: str | None = None
    progress: int | None = None


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


@router.get("/status/snapshot", response_model=UIStatusResponse)
async def get_ui_status_snapshot():
    """
    Get a one-shot snapshot of status information.

    Aggregates health, rate limit, and progenitor status into a single response.
    Uses real data from data/state/ files.

    For continuous updates, use the SSE stream at /status instead.
    """
    from . import main as chat_main

    state_reader = get_state_reader()

    try:
        # Get basic health from backend
        health = await chat_main.health_check_detailed()

        # Get state snapshot (includes loops, agents, backlog, schedule, build)
        snapshot = state_reader.get_snapshot()

        # Determine if progenitor is running from state file
        progenitor_running = snapshot.health.is_running()

        # Build info from snapshot
        build_info = None
        if snapshot.build:
            build_info = BuildInfoResponse(
                in_progress=snapshot.build.in_progress,
                phase=snapshot.build.phase,
                task_description=snapshot.build.task_description,
                started_at=snapshot.build.started_at,
                iteration=snapshot.build.iteration,
                score=snapshot.build.score,
                message_count=snapshot.build.message_count,
            )

        # Build response with full data
        return UIStatusResponse(
            connected=True,
            server_status=health.get("status", "unknown"),
            progenitor_running=progenitor_running,
            progenitor_status=snapshot.health.status,  # Raw status from health.json
            uptime_seconds=health.get("uptime_seconds", 0),
            version=health.get("version", "unknown"),
            memory_mb=health.get("memory", {}).get("rss_mb"),
            active_requests=health.get("shutdown", {}).get("active_requests", 0),
            rate_limit_minute_limit=health.get("rate_limiter", {}).get("requests_per_minute_limit", 60),
            rate_limit_hour_limit=health.get("rate_limiter", {}).get("requests_per_hour_limit", 500),
            # State data
            cycle_count=snapshot.health.cycle_count,
            run_id=snapshot.health.run_id,
            messages_count=len(state_reader.get_progenitor_state().get("messages", [])),
            pid=snapshot.health.pid,
            last_active=snapshot.health.last_active,
            # Build status
            build=build_info,
            # Lists
            loops=[
                LoopInfo(name=loop_item.name, status=loop_item.status, current_task=loop_item.current_task)
                for loop_item in snapshot.loops
            ],
            agents=[
                SubAgentInfo(
                    name=a.name,
                    status="ready" if progenitor_running else "offline",
                    kind=a.kind,
                    version=a.version,
                    description=a.description,
                    transport=a.transport,
                )
                for a in snapshot.agents
            ],
            backlog=[
                BacklogItemInfo(id=b.id, type=b.item_type, status=b.status, description=b.description)
                for b in snapshot.backlog
            ],
            schedule=[
                ScheduleItemInfo(id=s.id, name=s.name, schedule=s.schedule, next_run=s.next_run, enabled=s.enabled)
                for s in snapshot.schedule
            ],
        )
    except Exception as e:
        logger.error("Failed to get UI status: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/loops", response_model=LoopsResponse)
async def get_loops():
    """
    Get status of processing loops (Planner/Executor).
    """
    state_reader = get_state_reader()
    health = state_reader.get_health()
    loops = state_reader.get_loops(health)

    return LoopsResponse(
        loops=[
            LoopInfo(name=loop_item.name, status=loop_item.status, current_task=loop_item.current_task)
            for loop_item in loops
        ]
    )


@router.get("/agents", response_model=SubAgentsResponse)
async def get_sub_agents():
    """
    Get registered agents from registry.json.

    Returns information about all registered A2A agents.
    """
    state_reader = get_state_reader()
    agents, _ = state_reader.get_registry()
    health = state_reader.get_health()
    progenitor_running = health.is_running()

    # Convert to response format
    agent_infos = []
    for agent in agents:
        if agent.enabled:
            agent_infos.append(SubAgentInfo(
                name=agent.name,
                status="ready" if progenitor_running else "offline",
                kind=agent.kind,
                version=agent.version,
                description=agent.description,
                transport=agent.transport,
                task=None,
                progress=None,
            ))

    return SubAgentsResponse(agents=agent_infos, total=len(agent_infos))


@router.get("/backlog", response_model=BacklogResponse)
async def get_backlog():
    """
    Get backlog items from build_state.json.
    """
    state_reader = get_state_reader()
    backlog = state_reader.get_backlog()

    return BacklogResponse(
        items=[
            BacklogItemInfo(
                id=b.id,
                type=b.item_type,
                status=b.status,
                description=b.description,
            )
            for b in backlog
        ],
        total=len(backlog),
    )


@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule():
    """
    Get scheduled items from schedule.json.
    """
    state_reader = get_state_reader()
    schedule = state_reader.get_schedule()

    return ScheduleResponse(
        items=[
            ScheduleItemInfo(
                id=s.id,
                name=s.name,
                schedule=s.schedule,
                next_run=s.next_run,
                enabled=s.enabled,
            )
            for s in schedule
        ],
        total=len(schedule),
    )


class TelegramStatusResponse(BaseModel):
    """Telegram integration status."""
    enabled: bool
    connected: bool
    chat_id: str = ""  # Masked for security
    web_clients_active: int
    will_forward_to_telegram: bool
    rate_limit_per_minute: int


@router.get("/telegram/status", response_model=TelegramStatusResponse)
async def get_telegram_status():
    """
    Get Telegram integration status.

    Shows whether Telegram is enabled, connected, and whether
    messages will be forwarded to Telegram (no web clients connected).
    """
    from . import main as chat_main
    from .telegram_client import get_telegram_client

    telegram = get_telegram_client()
    subscriber_counts = get_subscriber_counts()
    config = chat_main.config

    # Mask the chat_id for security
    chat_id = config.telegram_chat_id
    masked_chat_id = f"{chat_id[:4]}...{chat_id[-4:]}" if len(chat_id) > 8 else "*" * len(chat_id)

    return TelegramStatusResponse(
        enabled=config.telegram_enabled,
        connected=telegram is not None and telegram._started,
        chat_id=masked_chat_id if config.telegram_enabled else "",
        web_clients_active=subscriber_counts["chat"],
        will_forward_to_telegram=(
            telegram is not None
            and telegram._started
            and subscriber_counts["chat"] == 0
        ),
        rate_limit_per_minute=config.telegram_rate_limit_per_minute,
    )


class TelegramTestRequest(BaseModel):
    """Request to test Telegram integration."""
    message: str = "Test message from Progenitor"


class TelegramTestResponse(BaseModel):
    """Response from Telegram test."""
    success: bool
    message: str


@router.post("/telegram/test", response_model=TelegramTestResponse)
async def test_telegram():
    """
    Send a test message to Telegram.

    Used to verify the Telegram integration is working correctly.
    """
    from .telegram_client import get_telegram_client

    telegram = get_telegram_client()
    if not telegram:
        return TelegramTestResponse(
            success=False,
            message="Telegram integration not enabled or not configured"
        )

    try:
        success = await telegram.send_message(
            "🧪 Test message from Progenitor Backend\n\n"
            "If you see this, Telegram integration is working!"
        )
        if success:
            return TelegramTestResponse(
                success=True,
                message="Test message sent successfully"
            )
        else:
            return TelegramTestResponse(
                success=False,
                message="Failed to send test message - check logs for details"
            )
    except Exception as e:
        logger.error("Telegram test failed: %s", e)
        return TelegramTestResponse(
            success=False,
            message=f"Error: {str(e)}"
        )


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


class AgentDetailResponse(BaseModel):
    """Detailed information about a single agent."""
    name: str
    status: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    transport: str = "stdio"
    enabled: bool = True
    # Statistics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    avg_response_time: float = 0.0
    last_active: str | None = None
    uptime_seconds: float = 0.0
    # Recent activity
    recent_requests: list[dict] = []
    # Performance
    success_rate: float = 100.0
    tokens_per_request_avg: float = 0.0


@router.get("/agents/{agent_name}", response_model=AgentDetailResponse)
async def get_agent_detail(agent_name: str):
    """
    Get detailed information about a specific agent.

    Returns agent configuration, statistics, and recent activity.
    """
    state_reader = get_state_reader()
    agents, _ = state_reader.get_registry()
    health = state_reader.get_health()
    progenitor_running = health.is_running()

    # Find the agent
    agent = next((a for a in agents if a.name == agent_name), None)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    return AgentDetailResponse(
        name=agent.name,
        status="ready" if progenitor_running else "offline",
        kind=agent.kind,
        version=agent.version,
        description=agent.description,
        transport=agent.transport,
        enabled=agent.enabled,
        uptime_seconds=getattr(health, "uptime_seconds", 0),
    )


@router.get("/prompts")
async def get_prompts():
    """
    Get available prompt templates.

    Returns list of prompt templates from prompts/ directory.
    """
    # TODO: Implement actual prompt loading from prompts/
    return {"prompts": []}


@router.get("/logs")
async def get_logs(
    level: str | None = None,
    search: str | None = None,
    limit: int = 500,
    format: str = "text",
):
    """
    Get agent logs with optional filtering.

    Args:
        level: Filter by log level (debug, info, warn, error)
        search: Search string to filter logs
        limit: Maximum number of lines to return (default 500)
        format: Response format - "text" for raw logs, "json" for structured

    Returns recent logs from the agent.
    """
    import json as json_module
    import re
    from pathlib import Path

    logs_dir = Path("logs")
    log_entries = []
    log_content = ""

    # Try to read the most recent log file
    try:
        log_files = list(logs_dir.glob("*.log")) + list(logs_dir.glob("*.jsonl"))
        if log_files:
            # Get most recent by modification time
            log_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            recent_log = log_files[0]

            # Read last N lines
            with open(recent_log, encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                lines = lines[-limit:]

            # Parse and filter logs
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Try to parse as JSON (jsonl format)
                try:
                    entry = json_module.loads(line)
                    log_level = entry.get("level", "info").lower()
                    message = entry.get("message", entry.get("msg", line))
                    timestamp = entry.get("timestamp", entry.get("time", ""))
                except json_module.JSONDecodeError:
                    # Parse standard log format: [TIMESTAMP] [LEVEL] MESSAGE
                    log_level = "info"
                    message = line
                    timestamp = ""

                    # Try to extract level from common patterns
                    level_match = re.search(r'\[(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\]', line, re.I)
                    if level_match:
                        log_level = level_match.group(1).lower()
                        if log_level == "warning":
                            log_level = "warn"

                    # Try to extract timestamp
                    time_match = re.search(r'^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', line)
                    if time_match:
                        timestamp = time_match.group(1)
                    entry = {"level": log_level, "message": message, "timestamp": timestamp}

                # Apply filters
                if level and log_level != level.lower():
                    continue
                if search and search.lower() not in message.lower():
                    continue

                log_entries.append({
                    "level": log_level,
                    "message": message,
                    "timestamp": timestamp,
                })

            log_content = '\n'.join(lines)
    except Exception as e:
        logger.warning("Failed to read logs: %s", e)

    if format == "json":
        return {
            "entries": log_entries,
            "total": len(log_entries),
            "filtered": level is not None or search is not None,
        }

    return {"logs": log_content, "total": len(log_entries)}


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


# Event subscribers for SSE - split into chat and status streams
_chat_subscribers: list[asyncio.Queue] = []  # For IPC events (chat, tasks, alerts)
_status_subscribers: list[asyncio.Queue] = []  # For state file status updates

# Shutdown event for SSE connections
_sse_shutdown_event: asyncio.Event | None = None

# Status monitor state
_status_monitor_task: asyncio.Task | None = None
_last_status: dict = {}

# Track active SSE response tasks so we can cancel them on shutdown
_active_chat_tasks: list[asyncio.Task] = []
_active_status_tasks: list[asyncio.Task] = []


def has_active_chat_subscribers() -> bool:
    """Check if there are any active SSE chat subscribers (web UI connected).

    Used by Telegram integration to determine whether to send messages to Telegram.
    If there are active chat subscribers, messages go to web UI and not Telegram.

    Returns:
        True if at least one chat subscriber is connected.
    """
    return len(_chat_subscribers) > 0


def get_subscriber_counts() -> dict[str, int]:
    """Get counts of all subscriber types.

    Returns:
        Dictionary with chat and status subscriber counts.
    """
    return {
        "chat": len(_chat_subscribers),
        "status": len(_status_subscribers),
        "total": len(_chat_subscribers) + len(_status_subscribers),
    }


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""
    from . import main as chat_main

    state_reader = get_state_reader()
    snapshot = state_reader.get_snapshot()

    now = time.time()
    start_time = getattr(chat_main, '_server_start_time', now)
    shutdown_mgr = getattr(chat_main, 'shutdown_manager', None)

    status = {
        "status": "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": snapshot.health.is_running(),
        "progenitor_status": snapshot.health.status,  # Raw status from health.json
        "version": getattr(chat_main.config, 'api_version', '0.1.0'),
        # State data
        "cycle_count": snapshot.health.cycle_count,  # Use health.cycle_count for accuracy
        "run_id": snapshot.health.run_id or snapshot.run_id,
        "messages_count": snapshot.messages_count,
        "pid": snapshot.health.pid,
        "last_active": snapshot.health.last_active,
        # Loops
        "loops": [
            {"name": loop_item.name, "status": loop_item.status, "current_task": loop_item.current_task}
            for loop_item in snapshot.loops
        ],
        # Agents
        "agents": [
            {
                "name": a.name,
                "kind": a.kind,
                "version": a.version,
                "description": a.description,
                "enabled": a.enabled,
                "status": "ready" if snapshot.health.is_running() else "offline",
            }
            for a in snapshot.agents
        ],
        # Backlog
        "backlog": [
            {"id": b.id, "type": b.item_type, "status": b.status, "description": b.description}
            for b in snapshot.backlog
        ],
        # Schedule
        "schedule": [
            {"id": s.id, "name": s.name, "schedule": s.schedule, "enabled": s.enabled}
            for s in snapshot.schedule
        ],
        # Build status
        "build": {
            "in_progress": snapshot.build.in_progress if snapshot.build else False,
            "phase": snapshot.build.phase if snapshot.build else None,
            "task_description": snapshot.build.task_description if snapshot.build else None,
            "started_at": snapshot.build.started_at if snapshot.build else None,
            "iteration": snapshot.build.iteration if snapshot.build else 0,
            "score": snapshot.build.score if snapshot.build else 0.0,
            "message_count": snapshot.build.message_count if snapshot.build else 0,
        } if snapshot.build else None,
    }

    if shutdown_mgr and shutdown_mgr.is_shutting_down:
        status["status"] = "draining"

    # Memory info
    try:
        import psutil

        process = psutil.Process()
        status["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except ImportError:
        status["memory_mb"] = None
    except Exception:
        status["memory_mb"] = None

    return status


async def _status_monitor():
    """Background task that polls status and broadcasts to SSE subscribers.

    Pushes status updates every 3 seconds for responsive UI.
    Pushes to _status_subscribers for status stream consumers.
    """
    global _last_status

    update_interval = 3  # Push status every 3 seconds for responsive updates

    logger.info("[StatusMonitor] Starting status monitor loop")

    while True:
        try:
            await asyncio.sleep(update_interval)

            # Count subscribers
            status_count = len(_status_subscribers)
            total_subscribers = status_count

            if total_subscribers == 0:
                continue  # No subscribers, skip

            current = await _get_current_status()
            _last_status = current.copy()

            # Push to /status subscribers
            broadcast_status_event("status", current)

            logger.debug(f"[StatusMonitor] Pushed status to {status_count} status subscribers")

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
    curr_mem: float = current.get("memory_mb") or 0
    prev_mem: float = previous.get("memory_mb") or 0
    return abs(curr_mem - prev_mem) > 10


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


def get_sse_shutdown_event() -> asyncio.Event:
    """Get or create the SSE shutdown event."""
    global _sse_shutdown_event
    if _sse_shutdown_event is None:
        _sse_shutdown_event = asyncio.Event()
    return _sse_shutdown_event


async def close_all_sse_connections():
    """
    Signal all SSE connections to close gracefully.

    Called during server shutdown to ensure SSE connections do not block
    the graceful shutdown process.
    """
    global _sse_shutdown_event, _active_chat_tasks, _active_status_tasks

    # Count all subscriber types
    chat_count = len(_chat_subscribers)
    status_count = len(_status_subscribers)
    total_count = chat_count + status_count

    logger.info("Closing SSE connections: %d chat, %d status (total: %d)",
                chat_count, status_count, total_count)

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = get_sse_shutdown_event()
    shutdown_event.set()

    # Send shutdown event to all subscriber types
    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}'
    }

    for queue in _chat_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    for queue in _status_subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(shutdown_data)

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    all_tasks = _active_chat_tasks + _active_status_tasks
    tasks_to_cancel = [t for t in all_tasks if not t.done()]
    if tasks_to_cancel:
        logger.info("Force-cancelling %d SSE tasks", len(tasks_to_cancel))
        for task in tasks_to_cancel:
            task.cancel()
        # Wait for cancellation with timeout
        done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0)
        if pending:
            logger.warning("%d SSE tasks did not cancel in time", len(pending))

    # Clear all lists
    _active_chat_tasks.clear()
    _active_status_tasks.clear()

    logger.info("SSE shutdown signal sent to all connections")


# ============================================================================
# NEW SPLIT SSE ENDPOINTS
# ============================================================================

async def _generate_chat_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for chat/IPC stream only.

    This stream receives:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages
    - alert: System alerts
    - task: Task status updates
    - agent: Agent activity updates

    Does NOT receive status/health updates (use /status for those).
    """

    queue: asyncio.Queue = asyncio.Queue()
    _chat_subscribers.append(queue)

    # Track current task for cancellation during shutdown
    current_task = asyncio.current_task()
    if current_task:
        _active_chat_tasks.append(current_task)

    # Get shutdown event to watch for server shutdown
    shutdown_event = get_sse_shutdown_event()

    try:
        # Send initial connection event
        yield f'event: connected\ndata: {{"stream": "chat", "timestamp": {time.time()}}}\n\n'

        while True:
            # Check if shutdown has been requested
            if shutdown_event.is_set():
                logger.debug("Chat SSE connection closing due to server shutdown")
                yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                break

            try:
                # Wait for event with timeout (30s keepalive)
                event = await asyncio.wait_for(queue.get(), timeout=30.0)

                # Check if this is a shutdown event
                if event.get('type') == 'shutdown':
                    yield f"event: shutdown\ndata: {event['data']}\n\n"
                    break

                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except TimeoutError:
                # Check shutdown again on timeout
                if shutdown_event.is_set():
                    yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                    break
                # Send heartbeat (no status payload - just keepalive)
                yield f'event: heartbeat\ndata: {{"timestamp": {time.time()}}}\n\n'
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            yield 'event: shutdown\ndata: {"reason": "connection_cancelled", "message": "Connection closed"}\n\n'
    finally:
        if queue in _chat_subscribers:
            _chat_subscribers.remove(queue)
        if current_task and current_task in _active_chat_tasks:
            _active_chat_tasks.remove(current_task)


async def _generate_status_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for status/health stream only.

    This stream receives:
    - status: Full status updates from state files
    - heartbeat: Periodic heartbeats with status

    Does NOT receive chat/IPC events (use /chat for those).
    """
    import json

    queue: asyncio.Queue = asyncio.Queue()
    _status_subscribers.append(queue)

    # Track current task for cancellation during shutdown
    current_task = asyncio.current_task()
    if current_task:
        _active_status_tasks.append(current_task)

    # Get shutdown event to watch for server shutdown
    shutdown_event = get_sse_shutdown_event()

    # Start status monitor if not running
    start_status_monitor()

    try:
        # Send initial connection event with current status
        initial_status = await _get_current_status()
        yield f"event: connected\ndata: {json.dumps(initial_status)}\n\n"

        while True:
            # Check if shutdown has been requested
            if shutdown_event.is_set():
                logger.debug("Status SSE connection closing due to server shutdown")
                yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                break

            try:
                # Wait for event with timeout (5s for responsive status updates)
                event = await asyncio.wait_for(queue.get(), timeout=5.0)

                # Check if this is a shutdown event
                if event.get('type') == 'shutdown':
                    yield f"event: shutdown\ndata: {event['data']}\n\n"
                    break

                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except TimeoutError:
                # Check shutdown again on timeout
                if shutdown_event.is_set():
                    yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                    break
                # Send heartbeat with current status
                heartbeat_status = await _get_current_status()
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat_status)}\n\n"
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            yield 'event: shutdown\ndata: {"reason": "connection_cancelled", "message": "Connection closed"}\n\n'
    finally:
        if queue in _status_subscribers:
            _status_subscribers.remove(queue)
        if current_task and current_task in _active_status_tasks:
            _active_status_tasks.remove(current_task)


@router.get("/events")
async def events_stream():
    """
    Server-Sent Events endpoint for IPC events.

    Connect to receive real-time notifications about:
    - scheduled_response: Scheduled task responses
    - agent_message: Agent-initiated messages (talk to user)
    - alert: System alerts requiring attention
    - task: Task status updates
    - agent: Agent activity updates

    This stream does NOT include status/health updates.
    Use /status for continuous health monitoring.

    Events are formatted as:
        event: <type>
        data: <json>

    Example event types:
    - scheduled_response: {"content": "...", "metadata": {...}, "timestamp": ...}
    - agent_message: {"content": "...", "metadata": {...}, "timestamp": ...}
    - alert: {"id": "...", "type": "...", "title": "...", "message": "...", "urgent": false}
    """
    return StreamingResponse(
        _generate_chat_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("/status")
async def status_stream():
    """
    Server-Sent Events endpoint for continuous status updates.

    Connect to receive real-time status updates about:
    - Server health and uptime
    - Progenitor agent status
    - Cycle count and activity
    - Memory usage
    - Registered agents and tools
    - Backlog and schedule items
    - Build status

    This stream does NOT include chat/IPC events.
    Use /events for chat messages and alerts.

    Events are formatted as:
        event: <type>
        data: <json>

    Event types:
    - connected: Initial connection with full status
    - status: Periodic status updates (every ~3 seconds)
    - heartbeat: Keepalive with status (during quiet periods)
    - shutdown: Server shutting down
    """
    return StreamingResponse(
        _generate_status_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


# ============================================================================
# BROADCAST FUNCTIONS
# ============================================================================

def broadcast_chat_event(event_type: str, data: dict):
    """
    Broadcast an event to /chat SSE subscribers.

    Use for IPC-related events like:
    - scheduled_response
    - agent_message
    - alert
    - task
    - agent

    Args:
        event_type: Type of event
        data: Event data as JSON-serializable dict
    """
    import json

    event = {
        "type": event_type,
        "data": json.dumps(data)
    }

    # Push to /chat subscribers
    for queue in _chat_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for chat subscriber")


def broadcast_status_event(event_type: str, data: dict):
    """
    Broadcast an event to /status SSE subscribers.

    Use for status/health events like:
    - status
    - heartbeat

    Does NOT broadcast to /events (chat stream).

    Args:
        event_type: Type of event
        data: Event data as JSON-serializable dict
    """
    import json

    event = {
        "type": event_type,
        "data": json.dumps(data)
    }

    # Push to /status subscribers only
    for queue in _status_subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full for status subscriber")


def broadcast_alert(alert: AlertInfo):
    """Broadcast a new alert to all subscribers."""
    # Alerts go to chat stream
    broadcast_chat_event("alert", {
        "id": alert.id,
        "type": alert.type,
        "title": alert.title,
        "message": alert.message,
        "urgent": alert.urgent
    })


def broadcast_status_change(status: str, details: dict | None = None):
    """Broadcast a status change to all subscribers."""
    # Status changes go to status stream
    broadcast_status_event("status", {
        "status": status,
        "details": details or {},
        "timestamp": time.time()
    })
