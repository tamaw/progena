"""
LangGraph Topology for Progenitor Agent - Outer Loop.

Implements the outer loop for handling user interactions:
Intake → TimeCheck → Decide → PickNext → Act → Respond → CycleLimit → [Idle]

Build tasks are handled directly by Decide which starts BackgroundBuildRunner.
The inner loop (with its own Planner) then runs:
OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN

No planner in the outer loop - decide handles all routing directly.
"""

import logging
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state.models import ProgenitorState
from .routing import (
    should_continue_outer_loop as should_continue,
    route_after_decide,
    route_after_act,
    route_after_validate,
    route_after_pick_next,
)
from .nodes import (
    intake,
    time_check,
    decide,
    pick_next,
    worktree_prep,
    act,
    validate,
    commit_core,
    respond,
    idle,
    cycle_limit,
)

logger = logging.getLogger(__name__)


def create_progenitor_graph(
    state_manager,
    llm_client,
    tool_registry,
    config,
    worktrees_dir,
    event_pusher=None,
    copilot_acp_manager=None,
):
    """Create the outer loop LangGraph for Progenitor.

    Outer Loop Flow:
    Intake → TimeCheck → Decide → PickNext → WorktreePrep →
    Act → [Validate] → CommitCore → Respond → CycleLimit → [Idle]

    No planner in outer loop. Decide handles all routing:
    - Direct answers go to respond
    - Builds start BackgroundBuildRunner (inner loop has its own planner)
    - Tool calls/agent calls go to pick_next for execution

    Args:
        state_manager: StateManager instance for persistence.
        llm_client: LLMClient instance for model calls.
        tool_registry: ToolRegistry instance for agent/tool discovery.
        config: ProgenitorConfig instance with all configuration.
        worktrees_dir: Path to worktrees directory.
        event_pusher: Optional callback to push events to clients via IPC.
        copilot_acp_manager: CopilotACPManager instance for ACP communication.

    Returns:
        Compiled StateGraph implementing the outer loop.
    """
    # Create node functions with dependencies injected
    nodes = {
        "intake": intake.create_node(state_manager),
        "time_check": time_check.create_node(config.runtime.timezone, state_manager),
        "decide": decide.create_node(llm_client, tool_registry, state_manager, config),
        "pick_next": pick_next.create_node(),
        "worktree_prep": worktree_prep.create_node(worktrees_dir),
        "act": act.create_node(
            llm_client, tool_registry, config, worktrees_dir, state_manager,
            event_pusher, copilot_acp_manager
        ),
        "validate": validate.create_node(config),
        "commit_core": commit_core.create_node(state_manager),
        "respond": respond.create_node(state_manager),
        "idle": idle.create_node(config, state_manager),
        "cycle_limit": cycle_limit.create_node(config),
    }

    # Build the graph
    builder = StateGraph(ProgenitorState)

    # Add all nodes
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)  # type: ignore[arg-type]

    # Set entry point
    builder.set_entry_point("intake")

    # Define edges
    builder.add_edge("intake", "time_check")
    builder.add_edge("time_check", "decide")

    # Conditional routing after decide
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "pick_next": "pick_next",
            "respond": "respond",
        }
    )

    # Conditional routing after pick_next
    builder.add_conditional_edges(
        "pick_next",
        route_after_pick_next,
        {
            "worktree_prep": "worktree_prep",
            "respond": "respond",
        }
    )

    builder.add_edge("worktree_prep", "act")

    # Conditional routing after act
    builder.add_conditional_edges(
        "act",
        route_after_act,
        {
            "validate": "validate",
            "commit_core": "commit_core",
        }
    )

    # Conditional routing after validate
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "pick_next": "pick_next",
            "commit_core": "commit_core",
        }
    )

    builder.add_edge("commit_core", "respond")
    builder.add_edge("respond", "cycle_limit")

    # Conditional routing after cycle_limit
    builder.add_conditional_edges(
        "cycle_limit",
        should_continue,
        {
            "pick_next": "pick_next",
            "idle": "idle",
            "end": END,
        }
    )

    # Idle loops back to intake
    builder.add_edge("idle", "intake")

    # Compile with checkpointing
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Created outer loop graph")
    return graph
