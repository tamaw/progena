"""LangGraph topology for background jobs.

This graph is executed by the ProgenitorLoop background worker.
It is intentionally separate from the user-chat outer loop graph.

Flow (when work is due):

- Scheduled tasks:
    ScheduleDispatch → Intake → Chat → Respond → ScheduleFinalize → END

- Backlog execution (no user message):
    ScheduleDispatch → PickNext → WorktreePrep → Act → [Validate] → CommitCore → END

When no work is due and no backlog is pending, the graph ends immediately.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from pathlib import Path

from .nodes import act, chat, commit_core, intake, pick_next, respond, validate, worktree_prep
from .nodes.schedule_dispatch import create_schedule_dispatch_node, create_schedule_finalize_node
from .routing import route_after_act
from .state.models import ProgenitorState

logger = logging.getLogger(__name__)


def create_background_graph(
    *,
    state_manager,
    llm_client,
    tool_registry,
    config,
    worktrees_dir: Path,
    copilot_acp_manager,
    event_pusher: Callable[[str, str, dict[str, Any] | None], Awaitable[None]] | None = None,
):
    timezone = config.runtime.timezone

    nodes = {
        "pick_due_schedule": create_schedule_dispatch_node(timezone, state_manager, event_pusher),

        # Scheduled task path
        "intake": intake.create_node(state_manager),
        "chat": chat.create_node(llm_client, tool_registry, state_manager, config),
        "respond": respond.create_node(state_manager),
        "advance_schedule": create_schedule_finalize_node(timezone, state_manager, event_pusher),

        # Backlog execution path
        "pick_next": pick_next.create_node(),
        "worktree_prep": worktree_prep.create_node(worktrees_dir),
        "act": act.create_node(
            llm_client,
            tool_registry,
            config,
            worktrees_dir,
            state_manager,
            event_pusher,
            copilot_acp_manager,
        ),
        "validate": validate.create_node(config, llm_client, state_manager),
        "commit_core": commit_core.create_node(state_manager),
    }

    builder = StateGraph(ProgenitorState)
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)  # type: ignore[arg-type]

    builder.set_entry_point("pick_due_schedule")

    def _route_after_pick(state: ProgenitorState) -> str:
        work_type = state.get("bg_work_type")
        if work_type == "scheduled":
            return "intake"
        if work_type == "backlog":
            return "pick_next"
        return "end"

    builder.add_conditional_edges(
        "pick_due_schedule",
        _route_after_pick,
        {
            "intake": "intake",
            "pick_next": "pick_next",
            "end": END,
        },
    )

    builder.add_edge("intake", "chat")
    builder.add_edge("chat", "respond")
    builder.add_edge("respond", "advance_schedule")
    builder.add_edge("advance_schedule", END)

    # Backlog execution edges
    builder.add_edge("pick_next", "worktree_prep")
    builder.add_edge("worktree_prep", "act")

    builder.add_conditional_edges(
        "act",
        route_after_act,
        {
            "validate": "validate",
            "commit_core": "commit_core",
        },
    )

    # In background execution, always persist state after validation (even on failures).
    builder.add_edge("validate", "commit_core")
    builder.add_edge("commit_core", END)

    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)
    logger.info("Created background job graph")
    return graph
