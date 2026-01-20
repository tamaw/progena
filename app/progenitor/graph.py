"""
LangGraph Topology for Progenitor Agent.

Implements the perpetual loop with nodes:
Intake → TimeCheck → EnqueueDue → Decide → Planner → PickNext → Act →
Validate → Register → CommitCore → Respond → Idle → (repeat)

The graph supports conditional routing based on decision type and work item state.
It is designed to run perpetually, processing user input and scheduled tasks.
"""

import logging
from typing import Literal, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state.models import ProgenitorState, WorkItem, Decision
from .nodes import (
    intake,
    time_check,
    enqueue_due,
    decide,
    planner,
    pick_next,
    worktree_prep,
    act,
    validate,
    register,
    commit_core,
    respond,
    idle,
    cycle_limit,
)

logger = logging.getLogger(__name__)


def should_continue(state: ProgenitorState) -> Literal["pick_next", "idle", "end"]:
    """Determine if we should continue processing, idle, or shutdown.

    Returns:
        'pick_next': More work items to process
        'idle': No pending work, enter idle state
        'end': Shutdown requested
    """
    if state.get("shutdown_requested", False):
        logger.info("Shutdown requested, ending loop")
        return "end"

    backlog = state.get("backlog", [])
    pending = []
    for item in backlog:
        if isinstance(item, WorkItem):
            if item.is_actionable:
                pending.append(item)
        elif isinstance(item, dict):
            status = item.get("status", "pending")
            if status in ("pending", "in_progress"):
                pending.append(item)

    if pending:
        logger.debug(f"{len(pending)} actionable items in backlog")
        return "pick_next"

    return "idle"


def route_after_decide(state: ProgenitorState) -> Literal["planner", "pick_next", "respond"]:
    """Route based on decision type.

    Routes to:
        - 'respond': For direct answers (no further action needed)
        - 'planner': For complex tasks requiring planning
        - 'pick_next': For simple delegation or tool use
    """
    decision = state.get("current_decision")

    if not decision:
        return "respond"

    # Handle both Decision objects and dicts
    if isinstance(decision, Decision):
        action = decision.action
        requires_planning = decision.requires_planning
    else:
        action = decision.get("action", "answer_directly")
        requires_planning = decision.get("requires_planning", False)

    # Get action value if it's an enum
    action_value = action.value if hasattr(action, 'value') else str(action)

    if action_value in ("answer", "answer_directly"):
        return "respond"

    if requires_planning:
        return "planner"

    return "pick_next"


def route_after_act(state: ProgenitorState) -> Literal["validate", "commit_core"]:
    """Route after action - validate if code was changed.

    Build operations (build_agent, build_mcp, upgrade) require validation
    before registration. Other operations skip directly to commit.
    """
    current_item = state.get("current_work_item")

    if current_item:
        # Handle both WorkItem objects and dicts
        if isinstance(current_item, WorkItem):
            item_type = current_item.item_type_value
        elif isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
        else:
            item_type = ""

        if item_type in ("build_agent", "build_mcp", "upgrade"):
            return "validate"

    return "commit_core"


def route_after_validate(state: ProgenitorState) -> Literal["register", "pick_next"]:
    """Route after validation.

    Successful validation proceeds to registration.
    Failed validation skips registration and picks next item.
    """
    validation_result = state.get("last_validation_result")

    if validation_result and validation_result.get("success", False):
        logger.debug("Validation passed, proceeding to register")
        return "register"

    # Failed validation - item will be updated and we pick next
    logger.warning("Validation failed, skipping registration")
    return "pick_next"


def route_after_pick_next(state: ProgenitorState) -> Literal["worktree_prep", "respond"]:
    """Route after picking next item - skip to respond if no work."""
    current_item = state.get("current_work_item")

    if current_item is None:
        return "respond"

    return "worktree_prep"


def create_progenitor_graph(
    state_manager,
    llm_client,
    tool_registry,
    config,
    worktrees_dir,
):
    """Create the LangGraph for Progenitor.

    Args:
        state_manager: StateManager instance for persistence.
        llm_client: LLMClient instance for model calls.
        tool_registry: ToolRegistry instance for agent/tool discovery.
        config: ProgenitorConfig instance with all configuration.
        worktrees_dir: Path to worktrees directory.

    Returns a compiled StateGraph that implements the perpetual loop:
    Intake → TimeCheck → EnqueueDue → Decide → [Planner] → PickNext →
    Act → [Validate → Register] → CommitCore → Respond → CycleLimit → [Idle]
    """

    # Create node functions with dependencies injected
    nodes = {
        "intake": intake.create_node(state_manager),
        "time_check": time_check.create_node(config.runtime.timezone),
        "enqueue_due": enqueue_due.create_node(state_manager),
        "decide": decide.create_node(llm_client, tool_registry, state_manager),
        "planner": planner.create_node(llm_client, state_manager),
        "pick_next": pick_next.create_node(),
        "worktree_prep": worktree_prep.create_node(worktrees_dir),
        "act": act.create_node(llm_client, tool_registry, config, worktrees_dir, state_manager),
        "validate": validate.create_node(config),
        "register": register.create_node(tool_registry, state_manager),
        "commit_core": commit_core.create_node(state_manager),
        "respond": respond.create_node(state_manager),
        "idle": idle.create_node(config, state_manager),
        "cycle_limit": cycle_limit.create_node(config),
    }

    # Build the graph
    builder = StateGraph(ProgenitorState)

    # Add all nodes
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)

    # Set entry point
    builder.set_entry_point("intake")

    # Define edges
    builder.add_edge("intake", "time_check")
    builder.add_edge("time_check", "enqueue_due")
    builder.add_edge("enqueue_due", "decide")

    # Conditional routing after decide
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "planner": "planner",
            "pick_next": "pick_next",
            "respond": "respond",
        }
    )

    builder.add_edge("planner", "pick_next")

    # Conditional routing after pick_next - skip worktree if no work item
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
            "register": "register",
            "pick_next": "pick_next",
        }
    )

    builder.add_edge("register", "commit_core")
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

    return graph
