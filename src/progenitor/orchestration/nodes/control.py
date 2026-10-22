"""Loop control and side-effect nodes."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime

from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.security.redaction import redact_secrets
from src.progenitor.utils import utcnow

logger = logging.getLogger(__name__)


async def idle_state_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Record one idle heartbeat tick."""
    del runtime
    return {
        "queued_input": None,
        "current_input": None,
        "current_input_role": None,
        "current_input_metadata": None,
        "requested_thread_id": None,
        "is_isolated_thread": False,
        "heartbeat_action": "idle",
        "idle_cycles": int(state.get("idle_cycles", 0) or 0) + 1,
        "health_status_override": None,
        "health_error": None,
    }


async def cleanup_cycle_state_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Clear per-cycle transient state."""
    del state, runtime
    return {
        "queued_input": None,
        "current_input": None,
        "current_input_role": None,
        "current_input_metadata": None,
        "requested_thread_id": None,
        "is_isolated_thread": False,
        "is_scheduled_input": False,
        "scheduled_schedule_id": None,
        "scheduled_run_id": None,
        "scheduled_message": None,
        "scheduled_result_status": None,
        "cycle_source": None,
        "cycle_error": None,
        "health_status_override": None,
        "health_error": None,
    }


async def cycle_error_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Apply error handling for a failed cycle."""
    safe_error = redact_secrets(str(state.get("cycle_error") or "Unknown error"))
    scheduled_run_id = str(state.get("scheduled_run_id") or "").strip() or None
    scheduled_schedule_id = str(state.get("scheduled_schedule_id") or "").strip() or None
    if scheduled_run_id:
        now = utcnow().isoformat()
        await runtime.context.state_manager.update_schedule_run(
            scheduled_run_id,
            {
                "status": "error",
                "result_status": "error",
                "error": safe_error,
                "updated_at": now,
                "completed_at": now,
            },
        )
        if scheduled_schedule_id:
            await runtime.context.state_manager.update_schedule_entry(
                scheduled_schedule_id,
                {
                    "last_run_status": "error",
                    "updated_at": now,
                    "completed_at": now,
                },
            )
    await runtime.context.queues.push_event("error", safe_error)
    return {
        "last_cycle_response": f"Error: {safe_error}",
        "cycle_error": None,
        "heartbeat_action": "error",
        "health_status_override": "error",
        "health_error": safe_error,
    }
