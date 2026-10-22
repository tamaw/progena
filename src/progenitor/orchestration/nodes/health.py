"""Health payload nodes."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - only used for a fixed-argv `git rev-parse`, no shell/user input
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.utils import utcnow_isoformat

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _detect_git_commit() -> str | None:
    """Return a short commit SHA identifying the running code, or None.

    Checked once at import time (the value can't change for the life of the
    process). Prefers an env var baked in at build time - the packaged Docker
    image has no `.git` directory, so `git rev-parse` only works in local dev.
    Existed to catch exactly the class of bug this repo hit repeatedly in one
    session: a long-running process silently executing stale code after a
    deploy, with no way to tell short of comparing Postgres document shapes.
    """
    env_commit = os.getenv("GIT_COMMIT") or os.getenv("GITHUB_SHA")
    if env_commit:
        return env_commit.strip()[:12]
    try:
        result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, no user input
            ["git", "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
            cwd=_PROJECT_ROOT,
        )
        commit = result.stdout.strip()
        return commit or None
    except Exception:
        return None


_GIT_COMMIT = _detect_git_commit()
_PROCESS_STARTED_AT = utcnow_isoformat()


async def write_health_status(
    state_manager: Any,
    status: str,
    *,
    thread_id: str | None = None,
    cycle_count: int = 0,
    payload: dict[str, Any] | None = None,
) -> None:
    """Persist a health status document via the StateManager."""
    health_data: dict[str, Any] = {
        "status": status,
        "last_active": utcnow_isoformat(),
        "thread_id": thread_id,
        "cycle_count": cycle_count,
        "pid": os.getpid(),
        "version": _GIT_COMMIT,
        "started_at": _PROCESS_STARTED_AT,
    }
    if payload:
        health_data.update(payload)
    await state_manager.save_health(health_data)


async def write_loop_health_status(
    state_manager: Any,
    state: Mapping[str, Any],
    *,
    status: str = "running",
    error: str | None = None,
    idle_cycles: int = 0,
    current_backoff_seconds: float = 0.0,
    queue_stats: dict[str, Any] | None = None,
    llm_model: str | None = None,
    llm_reasoning_enabled: bool | None = None,
    llm_reasoning_effort: str | None = None,
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
    if llm_model is not None:
        payload["llm_model"] = llm_model
    if llm_reasoning_enabled is not None:
        payload["llm_reasoning_enabled"] = llm_reasoning_enabled
    if llm_reasoning_effort is not None:
        payload["llm_reasoning_effort"] = llm_reasoning_effort
    await write_health_status(
        state_manager,
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
        runtime.context.state_manager,
        state,
        status=status,
        error=error,
        idle_cycles=_coerce_int(state.get("idle_cycles")),
        current_backoff_seconds=_coerce_float(state.get("current_backoff_seconds")),
        queue_stats=runtime.context.queues.get_queue_stats(),
        llm_model=runtime.context.llm_model,
        llm_reasoning_enabled=runtime.context.reasoning_enabled,
        llm_reasoning_effort=runtime.context.llm_reasoning_effort,
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
