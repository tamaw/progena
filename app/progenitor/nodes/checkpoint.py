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
    messages = serialize_messages(state.get("messages", []))
    tool_calls = state.get("tool_calls", [])
    attachments = state.get("attachments", [])
    checkpoint = {
        "thread_id": state.get("thread_id"),
        "cycle_count": state.get("cycle_count", 0),
        "actions_this_cycle": state.get("actions_this_cycle", 0),
        "shutdown_requested": state.get("shutdown_requested", False),
        "last_checkpoint": utcnow_isoformat(),
        "messages": messages,
        "tool_calls": tool_calls,
        "attachments": attachments,
        "current_input": state.get("current_input"),
        "pending_response": state.get("pending_response"),
        "last_reasoning_trace": state.get("last_reasoning_trace"),
    }
    await runtime.context.state_manager.save_current_state(checkpoint)
    if runtime.context.audit_log_sink is not None:
        await runtime.context.audit_log_sink.write_log(
            source="checkpoints",
            message="State checkpoint",
            payload={
                "event": "state_checkpoint",
                "thread_id": checkpoint["thread_id"],
                "cycle_count": checkpoint["cycle_count"],
                "actions_this_cycle": checkpoint["actions_this_cycle"],
                "message_count": len(messages),
                "tool_call_count": len(tool_calls),
                "attachment_count": len(attachments),
            },
            timestamp=checkpoint["last_checkpoint"],
            relative_path="state/current_state",
        )
    return {"last_checkpoint": checkpoint["last_checkpoint"]}
