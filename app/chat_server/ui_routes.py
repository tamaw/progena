"""
Chat UI API Routes.

Additional API endpoints specifically for the Chat UI frontend.

Reads real state data from data/state/ directory:
- health.json: Agent health status
- registry.json: Registered agents and tools
- build_state.json: Build state (messages, backlog, schedule, ideal_state)
- schedule.json: Scheduled tasks
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .state_reader import get_state_reader, StateSnapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui", tags=["ui"])


class BuildInfoResponse(BaseModel):
    """Build status information."""
    in_progress: bool = False
    phase: Optional[str] = None  # observe, think, plan, build, execute, verify, learn
    task_description: Optional[str] = None
    started_at: Optional[str] = None
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
    memory_mb: Optional[float] = None
    active_requests: int
    rate_limit_minute_limit: int
    rate_limit_hour_limit: int
    # State data
    cycle_count: int = 0
    run_id: Optional[str] = None
    messages_count: int = 0
    pid: Optional[int] = None
    last_active: Optional[str] = None
    # Build status
    build: Optional[BuildInfoResponse] = None
    # Lists
    loops: list["LoopInfo"] = []
    agents: list["SubAgentInfo"] = []
    backlog: list["BacklogItemInfo"] = []
    schedule: list["ScheduleItemInfo"] = []


class LoopInfo(BaseModel):
    """Processing loop information (Planner/Executor)."""
    name: str
    status: str
    current_task: Optional[str] = None


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
    task: Optional[str] = None
    progress: Optional[int] = None


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
    next_run: Optional[str]
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
    Uses real data from data/state/ files.

    This is the main status endpoint - the SSE /events endpoint pushes this same
    data structure every 3 seconds for real-time updates.
    """
    from . import main as chat_main

    state_reader = get_state_reader()

    try:
        # Get basic health from chat server
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
            messages_count=len(state_reader.get_current_state().get("messages", [])),
            pid=snapshot.health.pid,
            last_active=snapshot.health.last_active,
            # Build status
            build=build_info,
            # Lists
            loops=[
                LoopInfo(name=l.name, status=l.status, current_task=l.current_task)
                for l in snapshot.loops
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
            LoopInfo(name=l.name, status=l.status, current_task=l.current_task)
            for l in loops
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


class TokenHistoryEntry(BaseModel):
    """Single entry in token history."""
    timestamp: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    total_tokens: int
    model: str = "unknown"
    request_type: str = "chat"


class MetricsResponse(BaseModel):
    """Response containing detailed metrics."""
    timestamp: float
    session_id: Optional[str] = None
    # Token consumption
    tokens: dict = {
        "input_total": 0,
        "output_total": 0,
        "cached_total": 0,
        "total": 0,
        "session_input": 0,
        "session_output": 0,
    }
    # Token history for charts
    token_history: list[TokenHistoryEntry] = []
    # Cost estimation (USD)
    estimated_cost: float = 0.0
    cost_breakdown: dict = {
        "input_cost": 0.0,
        "output_cost": 0.0,
        "cached_cost": 0.0,
    }
    # Response times (last 100)
    response_times: list[float] = []
    response_time_avg: float = 0.0
    response_time_p95: float = 0.0
    response_time_p50: float = 0.0
    response_time_min: float = 0.0
    response_time_max: float = 0.0
    # Latency histogram buckets (for visualization)
    latency_histogram: dict = {
        "0-100ms": 0,
        "100-250ms": 0,
        "250-500ms": 0,
        "500-1000ms": 0,
        "1000-2500ms": 0,
        "2500ms+": 0,
    }
    # Throughput
    requests_per_minute: float = 0.0
    requests_per_hour: float = 0.0
    requests_total: int = 0
    successful_requests: int = 0
    # Errors
    error_count: int = 0
    error_rate: float = 0.0
    errors_by_type: dict = {}
    # Agent-specific
    cycles_per_hour: float = 0.0
    avg_cycle_duration: float = 0.0
    active_agents: int = 0


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
    last_active: Optional[str] = None
    uptime_seconds: float = 0.0
    # Recent activity
    recent_requests: list[dict] = []
    # Performance
    success_rate: float = 100.0
    tokens_per_request_avg: float = 0.0


# In-memory metrics storage with enhanced tracking
_metrics_data = {
    "tokens_input": 0,
    "tokens_output": 0,
    "tokens_cached": 0,
    "response_times": [],
    "requests_total": 0,
    "successful_requests": 0,
    "errors_total": 0,
    "errors_by_type": {},
    "session_start": time.time(),
    "token_history": [],  # List of TokenHistoryEntry dicts
    "agent_metrics": {},  # Per-agent metrics
    "latency_histogram": {
        "0-100ms": 0,
        "100-250ms": 0,
        "250-500ms": 0,
        "500-1000ms": 0,
        "1000-2500ms": 0,
        "2500ms+": 0,
    },
}

# Cost per 1000 tokens (approximate for GPT-4 Turbo)
_token_costs = {
    "input": 0.01,      # $0.01 per 1K input tokens
    "output": 0.03,     # $0.03 per 1K output tokens
    "cached": 0.0025,   # $0.0025 per 1K cached tokens
}


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    """
    Get detailed metrics for the dashboard.
    
    Returns token consumption, response times, throughput, and error rates.
    Used by the dashboard for charts and statistics.
    """
    now = time.time()
    session_duration = now - _metrics_data["session_start"]
    
    # Calculate response time stats
    times = _metrics_data["response_times"][-100:]  # Last 100
    avg_time = sum(times) / len(times) if times else 0
    sorted_times = sorted(times)
    p95_time = sorted_times[int(len(sorted_times) * 0.95)] if len(sorted_times) > 0 else 0
    p50_time = sorted_times[int(len(sorted_times) * 0.50)] if len(sorted_times) > 0 else 0
    min_time = min(times) if times else 0
    max_time = max(times) if times else 0
    
    # Calculate rates
    requests_per_minute = (_metrics_data["requests_total"] / session_duration) * 60 if session_duration > 0 else 0
    requests_per_hour = requests_per_minute * 60
    error_rate = (_metrics_data["errors_total"] / _metrics_data["requests_total"]) * 100 if _metrics_data["requests_total"] > 0 else 0
    
    # Calculate costs
    input_cost = (_metrics_data["tokens_input"] / 1000) * _token_costs["input"]
    output_cost = (_metrics_data["tokens_output"] / 1000) * _token_costs["output"]
    cached_cost = (_metrics_data["tokens_cached"] / 1000) * _token_costs["cached"]
    total_cost = input_cost + output_cost + cached_cost
    
    # Get active agents count
    state_reader = get_state_reader()
    agents, _ = state_reader.get_registry()
    active_agents = len([a for a in agents if a.enabled])
    
    # Prepare token history entries (convert dicts to TokenHistoryEntry)
    token_history = [
        TokenHistoryEntry(**entry) if isinstance(entry, dict) else entry
        for entry in _metrics_data.get("token_history", [])[-100:]
    ]
    
    return MetricsResponse(
        timestamp=now,
        tokens={
            "input_total": _metrics_data["tokens_input"],
            "output_total": _metrics_data["tokens_output"],
            "cached_total": _metrics_data["tokens_cached"],
            "total": _metrics_data["tokens_input"] + _metrics_data["tokens_output"],
            "session_input": _metrics_data["tokens_input"],
            "session_output": _metrics_data["tokens_output"],
        },
        token_history=token_history,
        estimated_cost=round(total_cost, 4),
        cost_breakdown={
            "input_cost": round(input_cost, 4),
            "output_cost": round(output_cost, 4),
            "cached_cost": round(cached_cost, 4),
        },
        response_times=times,
        response_time_avg=round(avg_time, 2),
        response_time_p95=round(p95_time, 2),
        response_time_p50=round(p50_time, 2),
        response_time_min=round(min_time, 2),
        response_time_max=round(max_time, 2),
        latency_histogram=_metrics_data.get("latency_histogram", {}),
        requests_per_minute=round(requests_per_minute, 2),
        requests_per_hour=round(requests_per_hour, 2),
        requests_total=_metrics_data["requests_total"],
        successful_requests=_metrics_data.get("successful_requests", 0),
        error_count=_metrics_data["errors_total"],
        error_rate=round(error_rate, 2),
        errors_by_type=_metrics_data.get("errors_by_type", {}),
        active_agents=active_agents,
    )


def record_request_metrics(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    response_time_ms: float = 0.0,
    is_error: bool = False,
    error_type: str = None,
    model: str = "unknown",
    request_type: str = "chat",
    agent_name: str = None,
):
    """
    Record metrics for a request.
    
    Called by chat handlers to track token consumption and response times.
    
    Args:
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens generated
        cached_tokens: Number of cached tokens used
        response_time_ms: Response time in milliseconds
        is_error: Whether the request resulted in an error
        error_type: Type of error (e.g., "timeout", "rate_limit", "server_error")
        model: Model used for the request
        request_type: Type of request (e.g., "chat", "completion", "embedding")
        agent_name: Name of the agent making the request
    """
    now = time.time()
    
    # Update token counts
    _metrics_data["tokens_input"] += input_tokens
    _metrics_data["tokens_output"] += output_tokens
    _metrics_data["tokens_cached"] += cached_tokens
    _metrics_data["requests_total"] += 1
    
    if not is_error:
        _metrics_data["successful_requests"] += 1
    
    # Add to token history
    _metrics_data["token_history"].append({
        "timestamp": now,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": input_tokens + output_tokens,
        "model": model,
        "request_type": request_type,
    })
    
    # Keep only last 1000 history entries
    if len(_metrics_data["token_history"]) > 1000:
        _metrics_data["token_history"] = _metrics_data["token_history"][-1000:]
    
    # Update response times and latency histogram
    if response_time_ms > 0:
        _metrics_data["response_times"].append(response_time_ms)
        # Keep only last 1000 response times
        if len(_metrics_data["response_times"]) > 1000:
            _metrics_data["response_times"] = _metrics_data["response_times"][-1000:]
        
        # Update latency histogram
        if response_time_ms < 100:
            _metrics_data["latency_histogram"]["0-100ms"] += 1
        elif response_time_ms < 250:
            _metrics_data["latency_histogram"]["100-250ms"] += 1
        elif response_time_ms < 500:
            _metrics_data["latency_histogram"]["250-500ms"] += 1
        elif response_time_ms < 1000:
            _metrics_data["latency_histogram"]["500-1000ms"] += 1
        elif response_time_ms < 2500:
            _metrics_data["latency_histogram"]["1000-2500ms"] += 1
        else:
            _metrics_data["latency_histogram"]["2500ms+"] += 1
    
    # Track errors
    if is_error:
        _metrics_data["errors_total"] += 1
        if error_type:
            _metrics_data["errors_by_type"][error_type] = \
                _metrics_data["errors_by_type"].get(error_type, 0) + 1
    
    # Track per-agent metrics
    if agent_name:
        if agent_name not in _metrics_data["agent_metrics"]:
            _metrics_data["agent_metrics"][agent_name] = {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "tokens_input": 0,
                "tokens_output": 0,
                "response_times": [],
                "last_active": None,
                "recent_requests": [],
            }
        
        agent_metrics = _metrics_data["agent_metrics"][agent_name]
        agent_metrics["total_requests"] += 1
        agent_metrics["tokens_input"] += input_tokens
        agent_metrics["tokens_output"] += output_tokens
        agent_metrics["last_active"] = now
        
        if is_error:
            agent_metrics["failed_requests"] += 1
        else:
            agent_metrics["successful_requests"] += 1
        
        if response_time_ms > 0:
            agent_metrics["response_times"].append(response_time_ms)
            if len(agent_metrics["response_times"]) > 100:
                agent_metrics["response_times"] = agent_metrics["response_times"][-100:]
        
        # Track recent requests
        agent_metrics["recent_requests"].append({
            "timestamp": now,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "response_time_ms": response_time_ms,
            "is_error": is_error,
        })
        if len(agent_metrics["recent_requests"]) > 20:
            agent_metrics["recent_requests"] = agent_metrics["recent_requests"][-20:]


def reset_metrics():
    """Reset all metrics (for testing)."""
    global _metrics_data
    _metrics_data = {
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cached": 0,
        "response_times": [],
        "requests_total": 0,
        "successful_requests": 0,
        "errors_total": 0,
        "errors_by_type": {},
        "session_start": time.time(),
        "token_history": [],
        "agent_metrics": {},
        "latency_histogram": {
            "0-100ms": 0,
            "100-250ms": 0,
            "250-500ms": 0,
            "500-1000ms": 0,
            "1000-2500ms": 0,
            "2500ms+": 0,
        },
    }


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
    
    # Get agent-specific metrics
    agent_metrics = _metrics_data.get("agent_metrics", {}).get(agent_name, {})
    
    # Calculate statistics
    total_requests = agent_metrics.get("total_requests", 0)
    successful_requests = agent_metrics.get("successful_requests", 0)
    failed_requests = agent_metrics.get("failed_requests", 0)
    tokens_input = agent_metrics.get("tokens_input", 0)
    tokens_output = agent_metrics.get("tokens_output", 0)
    response_times = agent_metrics.get("response_times", [])
    
    avg_response_time = sum(response_times) / len(response_times) if response_times else 0
    success_rate = (successful_requests / total_requests * 100) if total_requests > 0 else 100.0
    tokens_per_request = (tokens_input + tokens_output) / total_requests if total_requests > 0 else 0
    
    last_active = agent_metrics.get("last_active")
    if last_active:
        from datetime import datetime
        last_active = datetime.fromtimestamp(last_active).isoformat()
    
    return AgentDetailResponse(
        name=agent.name,
        status="ready" if progenitor_running else "offline",
        kind=agent.kind,
        version=agent.version,
        description=agent.description,
        transport=agent.transport,
        enabled=agent.enabled,
        total_requests=total_requests,
        successful_requests=successful_requests,
        failed_requests=failed_requests,
        total_tokens_input=tokens_input,
        total_tokens_output=tokens_output,
        avg_response_time=round(avg_response_time, 2),
        last_active=last_active,
        uptime_seconds=health.uptime_seconds if hasattr(health, 'uptime_seconds') else 0,
        recent_requests=agent_metrics.get("recent_requests", []),
        success_rate=round(success_rate, 2),
        tokens_per_request_avg=round(tokens_per_request, 2),
    )


@router.get("/prompts")
async def get_prompts():
    """
    Get available prompt templates.

    Returns list of prompt templates from data/prompts/ directory.
    """
    # TODO: Implement actual prompt loading from data/prompts/
    return {"prompts": []}


@router.get("/logs")
async def get_logs(
    level: Optional[str] = None,
    search: Optional[str] = None,
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
    from pathlib import Path
    import json as json_module
    import re
    
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
            with open(recent_log, 'r', encoding='utf-8', errors='ignore') as f:
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


# Event subscribers for SSE
_event_subscribers: list[asyncio.Queue] = []

# Shutdown event for SSE connections
_sse_shutdown_event: Optional[asyncio.Event] = None

# Status monitor state
_status_monitor_task: Optional[asyncio.Task] = None
_last_status: dict = {}

# Track active SSE response tasks so we can cancel them on shutdown
_active_sse_tasks: list[asyncio.Task] = []


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""
    from . import main as chat_main
    import psutil

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
            {"name": l.name, "status": l.status, "current_task": l.current_task}
            for l in snapshot.loops
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
        process = psutil.Process()
        status["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except Exception:
        status["memory_mb"] = None

    return status


async def _status_monitor():
    """Background task that polls status and broadcasts to SSE subscribers.

    Pushes status updates every 3 seconds for responsive UI.
    """
    global _last_status

    update_interval = 3  # Push status every 3 seconds for responsive updates

    logger.info("[StatusMonitor] Starting status monitor loop")

    while True:
        try:
            await asyncio.sleep(update_interval)

            subscriber_count = len(_event_subscribers)
            if subscriber_count == 0:
                continue  # No subscribers, skip

            current = await _get_current_status()
            _last_status = current.copy()
            broadcast_event("status", current)
            logger.debug(f"[StatusMonitor] Pushed status to {subscriber_count} subscribers")

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
    global _sse_shutdown_event, _active_sse_tasks

    subscriber_count = len(_event_subscribers)
    logger.info("Closing %d SSE connections for shutdown", subscriber_count)

    # Set the shutdown event to signal all SSE generators to exit
    shutdown_event = get_sse_shutdown_event()
    shutdown_event.set()

    # Send shutdown event to all subscribers so they can close gracefully
    shutdown_data = {
        "type": "shutdown",
        "data": '{"reason": "server_shutdown", "message": "Server is shutting down"}'
    }
    for queue in _event_subscribers:
        try:
            queue.put_nowait(shutdown_data)
        except asyncio.QueueFull:
            pass

    # Give connections a moment to receive the shutdown message and exit cleanly
    await asyncio.sleep(0.5)

    # Stop the status monitor
    stop_status_monitor()

    # Cancel any remaining SSE tasks that have not exited
    tasks_to_cancel = [t for t in _active_sse_tasks if not t.done()]
    if tasks_to_cancel:
        logger.info("Force-cancelling %d SSE tasks", len(tasks_to_cancel))
        for task in tasks_to_cancel:
            task.cancel()
        # Wait for cancellation with timeout
        done, pending = await asyncio.wait(tasks_to_cancel, timeout=2.0)
        if pending:
            logger.warning("%d SSE tasks did not cancel in time", len(pending))

    # Clear the list
    _active_sse_tasks.clear()

    logger.info("SSE shutdown signal sent to all connections")


async def _generate_events() -> AsyncGenerator[str, None]:
    """Generate SSE events for a subscriber."""
    import json

    queue: asyncio.Queue = asyncio.Queue()
    _event_subscribers.append(queue)

    # Track current task for cancellation during shutdown
    current_task = asyncio.current_task()
    if current_task:
        _active_sse_tasks.append(current_task)

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
                logger.debug("SSE connection closing due to server shutdown")
                yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                break

            try:
                # Wait for event with timeout (30s keepalive)
                # Use shorter timeout during shutdown check
                event = await asyncio.wait_for(queue.get(), timeout=5.0)

                # Check if this is a shutdown event
                if event.get('type') == 'shutdown':
                    yield f"event: shutdown\ndata: {event['data']}\n\n"
                    break

                yield f"event: {event['type']}\ndata: {event['data']}\n\n"
            except asyncio.TimeoutError:
                # Check shutdown again on timeout
                if shutdown_event.is_set():
                    yield 'event: shutdown\ndata: {"reason": "server_shutdown", "message": "Server is shutting down"}\n\n'
                    break
                # Send heartbeat with current status (keeps connection alive)
                heartbeat_status = await _get_current_status()
                yield f"event: heartbeat\ndata: {json.dumps(heartbeat_status)}\n\n"
    except asyncio.CancelledError:
        # Send shutdown event on cancellation
        try:
            yield 'event: shutdown\ndata: {"reason": "connection_cancelled", "message": "Connection closed"}\n\n'
        except Exception:
            pass
    finally:
        if queue in _event_subscribers:
            _event_subscribers.remove(queue)
        # Remove from active tasks
        if current_task and current_task in _active_sse_tasks:
            _active_sse_tasks.remove(current_task)


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


def broadcast_status_change(status: str, details: Optional[dict] = None):
    """Broadcast a status change to all subscribers."""
    broadcast_event("status", {
        "status": status,
        "details": details or {},
        "timestamp": time.time()
    })
