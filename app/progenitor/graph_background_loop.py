"""LangGraph topology for background jobs (scheduled tasks).

This graph is executed by the ProgenitorLoop background worker.
It is intentionally separate from the user-chat outer loop graph.

Flow (when work is due):
PickDueSchedule → Intake → Chat → Respond → AdvanceSchedule → END

When no work is due, the graph ends immediately after PickDueSchedule.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .nodes import chat, intake, respond
from .nodes.background_schedule import create_advance_node, create_pick_due_node
from .state.models import ProgenitorState

logger = logging.getLogger(__name__)


def create_background_graph(
    *,
    state_manager,
    llm_client,
    tool_registry,
    config,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
):
    timezone = config.runtime.timezone

    nodes = {
        "pick_due_schedule": create_pick_due_node(timezone, state_manager, event_pusher),
        "intake": intake.create_node(state_manager),
        "chat": chat.create_node(llm_client, tool_registry, state_manager, config),
        "respond": respond.create_node(state_manager),
        "advance_schedule": create_advance_node(timezone, state_manager, event_pusher),
    }

    builder = StateGraph(ProgenitorState)
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)  # type: ignore[arg-type]

    builder.set_entry_point("pick_due_schedule")

    def _route_after_pick(state: ProgenitorState) -> str:
        return "intake" if state.get("bg_had_work") else "end"

    builder.add_conditional_edges(
        "pick_due_schedule",
        _route_after_pick,
        {
            "intake": "intake",
            "end": END,
        },
    )

    builder.add_edge("intake", "chat")
    builder.add_edge("chat", "respond")
    builder.add_edge("respond", "advance_schedule")
    builder.add_edge("advance_schedule", END)

    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)
    logger.info("Created background job graph")
    return graph
