"""State checkpoint nodes."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.utils import utcnow_isoformat


async def checkpoint_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist the recoverable conversation state.

    Messages live exclusively in the LangGraph checkpoint; only scalar
    bookkeeping fields are stored in the per-thread ``current_state``
    document.  A bare-key mirror write preserves the health widget's
    ``get_snapshot()`` behaviour.
    """
    thread_id = str(state.get("thread_id") or "").strip() or None
    checkpoint = {
        "thread_id": thread_id,
        "cycle_count": state.get("cycle_count", 0),
        "actions_this_cycle": state.get("actions_this_cycle", 0),
        "shutdown_requested": state.get("shutdown_requested", False),
        "last_checkpoint": utcnow_isoformat(),
        "current_input": state.get("current_input"),
        "pending_response": state.get("pending_response"),
        "last_reasoning_trace": state.get("last_reasoning_trace"),
    }
    await runtime.context.state_manager.save_current_state(checkpoint, thread_id=thread_id)
    if runtime.context.audit_log_sink is not None:
        await runtime.context.audit_log_sink.write_log(
            source="checkpoints",
            message="State checkpoint",
            payload={
                "event": "state_checkpoint",
                "thread_id": checkpoint["thread_id"],
                "cycle_count": checkpoint["cycle_count"],
                "actions_this_cycle": checkpoint["actions_this_cycle"],
            },
            timestamp=checkpoint["last_checkpoint"],
            relative_path="state/current_state",
        )
    return {"last_checkpoint": checkpoint["last_checkpoint"]}
