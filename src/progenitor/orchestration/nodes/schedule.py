"""Scheduled work nodes."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.schedules.dispatch import (
    emit_scheduled_completion,
    enqueue_due_schedule_entries,
)
from src.progenitor.utils import utcnow, utcnow_isoformat


async def enqueue_due_schedule_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Enqueue due scheduled work and update schedule state."""
    enqueued_count = await enqueue_due_schedule_entries(
        runtime.context.state_manager,
        runtime.context.queues,
        current_time=utcnow(),
    )
    return {"scheduled_enqueued_count": enqueued_count}


async def scheduled_completion_node(
    state: dict[str, Any],
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Emit completion events for scheduled work."""
    return await emit_scheduled_completion(
        state,
        runtime.context.state_manager,
        runtime.context.queues,
    )


async def scheduler_tick_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Scan schedules when the heartbeat runtime marks the tick due."""
    if not runtime.context.scheduler_enabled:
        return {}
    if "schedule_tick" not in runtime.context.due_maintenance:
        return {}
    updates = await enqueue_due_schedule_node(_state, runtime)
    updates["last_schedule_poll_at"] = utcnow_isoformat()
    return updates
