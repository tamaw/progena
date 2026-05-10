"""Response extraction nodes."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from ..runtime.messages import extract_message_text, extract_reasoning_trace, get_last_ai_message
from ..state.models import ProgenitorState
from .context import HeartbeatContext


async def response_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Extract the final response text and any reasoning trace."""
    del runtime
    pending = state.get("pending_response")
    if pending:
        # Prefer an explicit pending response if the graph already set one.
        updates: dict[str, Any] = {"last_cycle_response": str(pending), "pending_response": None}
    else:
        # Otherwise, derive the answer from the latest assistant message.
        text = extract_message_text(get_last_ai_message(state.get("messages")))
        if not text:
            updates = {"pending_response": None}
        else:
            updates = {"last_cycle_response": text, "pending_response": None}
    reasoning_trace = extract_reasoning_trace(get_last_ai_message(state.get("messages")))
    if reasoning_trace:
        updates["last_reasoning_trace"] = reasoning_trace
    return updates
