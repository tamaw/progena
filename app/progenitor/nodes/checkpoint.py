"""State checkpoint nodes."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from ..runtime.messages import serialize_messages
from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext


async def checkpoint_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist the recoverable conversation state."""
    checkpoint = {
        "thread_id": state.get("thread_id"),
        "cycle_count": state.get("cycle_count", 0),
        "actions_this_cycle": state.get("actions_this_cycle", 0),
        "shutdown_requested": state.get("shutdown_requested", False),
        "last_checkpoint": utcnow_isoformat(),
        "messages": serialize_messages(state.get("messages", [])),
        "tool_calls": state.get("tool_calls", []),
        "attachments": state.get("attachments", []),
        "current_input": state.get("current_input"),
        "pending_response": state.get("pending_response"),
        "last_reasoning_trace": state.get("last_reasoning_trace"),
    }
    await runtime.context.state_manager.save_current_state(checkpoint)
    return {"last_checkpoint": checkpoint["last_checkpoint"]}
