"""DeepAgent chat node."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from ..state.models import ProgenitorState
from .context import HeartbeatContext


async def chat_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    current_input = str(state.get("current_input") or "").strip()
    if not current_input:
        return {}

    thread_id = str(state.get("thread_id") or "progenitor_main")
    input_metadata = state.get("current_input_metadata")
    additional_kwargs: dict[str, Any] = {"created_at": time.time()}
    if isinstance(input_metadata, dict) and isinstance(input_metadata.get("attachments"), list):
        additional_kwargs["attachments"] = input_metadata["attachments"]
    # DeepAgent returns the updated conversation state for this turn.
    human_message = HumanMessage(content=current_input, additional_kwargs=additional_kwargs)
    result = await runtime.context.agent_graph.ainvoke(
        {"messages": [human_message]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result if isinstance(result, dict) else {"messages": []}
