"""Health payload nodes."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext

HEALTH_FILE = "health.json"


async def health_payload_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Prepare a runtime health payload."""
    del runtime
    return {
        "health": {
            "status": "ok",
            "timestamp": utcnow_isoformat(),
            "thread_id": state.get("thread_id"),
            "cycle_count": state.get("cycle_count", 0),
        }
    }


async def write_health_status(
    state_dir: str | Path,
    status: str,
    *,
    thread_id: str | None = None,
    cycle_count: int = 0,
    payload: dict[str, Any] | None = None,
) -> None:
    """Write a health.json payload for runtime monitoring."""
    health_path = Path(state_dir) / HEALTH_FILE
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_data: dict[str, Any] = {
        "status": status,
        "last_active": utcnow_isoformat(),
        "thread_id": thread_id,
        "cycle_count": cycle_count,
        "pid": os.getpid(),
    }
    if payload:
        health_data.update(payload)
    health_path.write_text(json.dumps(health_data), encoding="utf-8")


async def write_loop_health_status(
    state_dir: str | Path,
    state: Mapping[str, Any],
    *,
    status: str = "running",
    error: str | None = None,
    idle_cycles: int = 0,
    current_backoff_seconds: float = 0.0,
    queue_stats: dict[str, Any] | None = None,
) -> None:
    """Write loop-oriented runtime health information."""
    payload: dict[str, Any] = {
        "idle_cycles": idle_cycles,
        "current_backoff_seconds": current_backoff_seconds,
        "message_count": len(state.get("messages") or [])
        if isinstance(state.get("messages"), list)
        else 0,
        "agent_count": len(state.get("agents") or [])
        if isinstance(state.get("agents"), list)
        else 0,
    }
    if queue_stats is not None:
        payload["queues"] = queue_stats
    if error:
        payload["error"] = error
    await write_health_status(
        state_dir,
        status,
        thread_id=str(state.get("thread_id") or "") or None,
        cycle_count=int(state.get("cycle_count", 0) or 0),
        payload=payload,
    )


async def runtime_health_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Write runtime health using state-provided overrides."""
    status = str(state.get("health_status_override") or "running")
    error = str(state.get("health_error") or "").strip() or None
    await write_loop_health_status(
        runtime.context.health_state_dir,
        state,
        status=status,
        error=error,
        idle_cycles=_coerce_int(state.get("idle_cycles")),
        current_backoff_seconds=_coerce_float(state.get("current_backoff_seconds")),
        queue_stats=runtime.context.queues.get_queue_stats(),
    )
    return {"health_status": status, "last_health_write_at": utcnow_isoformat()}


async def idle_health_tick_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Periodically write idle-loop health."""
    if "idle_health" not in runtime.context.due_maintenance:
        return {}
    return await runtime_health_node(state, runtime)


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0
