"""Runtime health file writing."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from .time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)

HEALTH_FILE = "health.json"


async def update_health_file(
    *,
    state_dir: Path,
    state: dict[str, Any],
    queue_stats: dict[str, Any],
    idle_cycles: int,
    current_backoff_seconds: float,
    status: str = "running",
    error: str | None = None,
) -> None:
    """Update health file for Docker health checks."""
    health_path = state_dir / HEALTH_FILE
    current_thread_id = state.get("thread_id")
    health_data = {
        "status": status,
        "last_active": utcnow_isoformat(),
        "thread_id": current_thread_id,
        "cycle_count": state.get("cycle_count", 0),
        "pid": os.getpid(),
        "idle_cycles": idle_cycles,
        "current_backoff_seconds": current_backoff_seconds,
        "message_count": len(state.get("messages") or [])
        if isinstance(state.get("messages"), list)
        else 0,
        "agent_count": len(state.get("agents") or [])
        if isinstance(state.get("agents"), list)
        else 0,
        "queues": queue_stats,
    }

    if error:
        health_data["error"] = error

    try:
        health_path.write_text(json.dumps(health_data))
    except Exception as e:
        logger.warning("Failed to update health file: %s", e)
