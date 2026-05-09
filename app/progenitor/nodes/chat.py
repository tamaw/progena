"""DeepAgent chat node."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from ..state.models import ProgenitorState
from .context import HeartbeatContext


async def chat_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Send one user message into the compiled DeepAgent graph."""
    current_input = str(state.get("current_input") or "").strip()
    if not current_input:
        return {}

    thread_id = str(state.get("thread_id") or "progenitor_main")
    # DeepAgent returns the updated conversation state for this turn.
    result = await runtime.context.agent_graph.ainvoke(
        {"messages": [HumanMessage(content=current_input)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result if isinstance(result, dict) else {"messages": []}
