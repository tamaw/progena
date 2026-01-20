"""
LangGraph Topology for Progenitor Agent - Dual Loop Architecture (loop.spec.md)

Implements:
- OUTER LOOP: Ideal State management (define, refine, verify)
- INNER LOOP: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN

The inner loop repeats until verification meets stop conditions or limits reached.
The outer loop refines ideal state continuously.
"""

import logging
import time
import uuid
from typing import Literal, Any
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state.models import ProgenitorState
from .nodes import (
    intake,
    time_check,
    enqueue_due,
    decide,
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

# Import new loop.spec.md nodes
from .nodes import observe, think, planner, execute, verify_loop, learn

logger = logging.getLogger(__name__)


def should_iterate(state: ProgenitorState) -> Literal["observe", "respond"]:
    """
    Determine if inner loop should continue iterating.

    Checks stop conditions:
    - Min score reached
    - Max iterations hit
    - Tool/time budget exhausted

    Returns:
        'observe': Continue iterating (next iteration)
        'respond': Stop and respond (verification passed or limits hit)
    """
    iteration = state.get("current_iteration", 0)
    verification_report = state.get("last_verification_report")
    ideal_state = state.get("ideal_state")

    # First iteration - always continue
    if iteration == 0:
        return "observe"

    # Check if verification passed
    if state.get("verification_passed", False):
        logger.info(f"Verification passed at iteration {iteration}")
        state["should_iterate"] = False
        state["iteration_stop_reason"] = "Verification passed"
        return "respond"

    # Check stop conditions from ideal state
    if ideal_state and verification_report:
        verification_score = state.get("verification_score", 0.0)
        actions_count = state.get("actions_this_cycle", 0)

        iteration_start = state.get("iteration_start_time", time.time())
        elapsed_time = time.time() - iteration_start

        should_stop, reasons = ideal_state.check_stop_conditions(
            current_score=verification_score,
            iteration=iteration,
            elapsed_time=elapsed_time,
            tool_calls=actions_count,
        )

        if should_stop:
            logger.info(f"Stop conditions met: {', '.join(reasons)}")
            state["should_iterate"] = False
            state["iteration_stop_reason"] = reasons[0] if reasons else "Stop condition met"
            return "respond"

    # Default: Continue iterating if under reasonable limits
    max_iterations = 5  # Failsafe
    if iteration >= max_iterations:
        logger.warning(f"Max failsafe iterations ({max_iterations}) reached")
        state["should_iterate"] = False
        state["iteration_stop_reason"] = f"Max iterations {max_iterations} reached"
        return "respond"

    # Continue iterating
    logger.info(f"Continuing to iteration {iteration + 1}")
    state["should_iterate"] = True
    return "observe"


def should_continue_outer(state: ProgenitorState) -> Literal["pick_next", "idle", "end"]:
    """
    Outer loop control - same as original.
    Determine if we should continue processing, idle, or shutdown.
    """
    if state.get("shutdown_requested", False):
        logger.info("Shutdown requested, ending loop")
        return "end"

    backlog = state.get("backlog", [])
    pending = []
    for item in backlog:
        if hasattr(item, "is_actionable"):
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


def route_after_decide(state: ProgenitorState) -> Literal["observe", "pick_next", "respond"]:
    """
    Route based on decision type.

    For complex tasks requiring iteration:
        → 'observe' (enter inner loop)
    For simple tasks:
        → 'pick_next' or 'respond'
    """
    decision = state.get("current_decision")

    if not decision:
        return "respond"

    # Handle both Decision objects and dicts
    if hasattr(decision, "action"):
        action = decision.action
        requires_planning = decision.requires_planning
    elif isinstance(decision, dict):
        action = decision.get("action", "answer_directly")
        requires_planning = decision.get("requires_planning", False)
    else:
        return "respond"

    # Get action value if it's an enum
    action_value = action.value if hasattr(action, 'value') else str(action)

    # Direct answers bypass inner loop
    if action_value in ("answer", "answer_directly"):
        return "respond"

    # Complex tasks enter inner loop with OBSERVE
    if requires_planning or action_value in ("build_agent", "build_mcp", "upgrade"):
        logger.info("Entering inner loop (OBSERVE phase)")
        # Initialize iteration state (loop.spec.md Task State)
        state["current_iteration"] = 0
        state["iteration_start_time"] = time.time()
        state["should_iterate"] = True
        state["lessons_learned"] = []
        # Initialize artefact tracking (loop.spec.md Task State)
        state["task_id"] = f"task_{uuid.uuid4().hex[:8]}"
        state["artefacts"] = []
        state["best_artefact"] = None
        state["best_artefact_score"] = 0.0
        state["verification_reports"] = []
        return "observe"

    # Simple tasks go to pick_next
    return "pick_next"


def route_after_act_build(state: ProgenitorState) -> Literal["validate", "execute"]:
    """
    Route after BUILD phase per loop.spec.md.

    Build operations that produce code → validate (then execute)
    Other operations → execute directly

    Per loop.spec.md: BUILD → EXECUTE → VERIFY
    """
    current_item = state.get("current_work_item")

    if current_item:
        # Handle both WorkItem objects and dicts
        if hasattr(current_item, "item_type_value"):
            item_type = current_item.item_type_value
        elif isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
        else:
            item_type = ""

        # Code-producing builds need validation before execution
        if item_type in ("build_agent", "build_mcp", "upgrade"):
            return "validate"

    # Everything else goes to execute
    return "execute"


def route_after_validate(state: ProgenitorState) -> Literal["register", "execute"]:
    """
    Route after validation.

    Per loop.spec.md: After validation, proceed to EXECUTE phase.
    Successful builds may register first, then execute.
    """
    validation_result = state.get("last_validation_result")

    # If validation succeeded and it's a build, register first
    if validation_result:
        if hasattr(validation_result, "success"):
            success = validation_result.success
        elif isinstance(validation_result, dict):
            success = validation_result.get("success", False)
        else:
            success = False

        # Successful build validation → register (then execute)
        current_item = state.get("current_work_item")
        if success and current_item:
            item_type = ""
            if hasattr(current_item, "item_type_value"):
                item_type = current_item.item_type_value
            elif isinstance(current_item, dict):
                item_type = current_item.get("item_type", "")

            if item_type in ("build_agent", "build_mcp"):
                return "register"

    # Default: go straight to execute
    return "execute"


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
    """
    Create the dual-loop LangGraph for Progenitor (loop.spec.md).

    OUTER LOOP (ideal state management):
    Intake → TimeCheck → EnqueueDue → Decide → [Inner Loop] → Respond → CycleLimit → [Idle]

    INNER LOOP (iteration until verification passes):
    OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → [iterate or respond]

    Args:
        state_manager: StateManager instance for persistence.
        llm_client: LLMClient instance for model calls.
        tool_registry: ToolRegistry instance for agent/tool discovery.
        config: ProgenitorConfig instance with all configuration.
        worktrees_dir: Path to worktrees directory.

    Returns:
        Compiled StateGraph implementing dual-loop architecture.
    """

    # Create all node functions
    nodes = {
        # Outer loop nodes (original)
        "intake": intake.create_node(state_manager),
        "time_check": time_check.create_node(config.runtime.timezone),
        "enqueue_due": enqueue_due.create_node(state_manager),
        "decide": decide.create_node(llm_client, tool_registry, state_manager),
        "pick_next": pick_next.create_node(),
        "worktree_prep": worktree_prep.create_node(worktrees_dir),
        "validate": validate.create_node(config),
        "register": register.create_node(tool_registry, state_manager),
        "commit_core": commit_core.create_node(state_manager),
        "respond": respond.create_node(state_manager),
        "idle": idle.create_node(config, state_manager),
        "cycle_limit": cycle_limit.create_node(config),

        # Inner loop nodes (loop.spec.md)
        "observe": observe.create_node(llm_client, state_manager),
        "think": think.create_node(llm_client, state_manager),
        "plan": planner.create_node(llm_client, state_manager),  # PLAN phase uses planner
        "build": act.create_node(llm_client, tool_registry, config, worktrees_dir, state_manager),
        "execute": execute.create_node(tool_registry, state_manager),  # EXECUTE phase (loop.spec.md)
        "verify": verify_loop.create_node(llm_client, state_manager),
        "learn": learn.create_node(llm_client, state_manager),
    }

    # Build the graph
    builder = StateGraph(ProgenitorState)

    # Add all nodes
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)  # type: ignore[arg-type]

    # OUTER LOOP EDGES
    builder.set_entry_point("intake")
    builder.add_edge("intake", "time_check")
    builder.add_edge("time_check", "enqueue_due")
    builder.add_edge("enqueue_due", "decide")

    # After decide: route to inner loop, backlog, or respond
    builder.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "observe": "observe",  # Enter inner loop
            "pick_next": "pick_next",
            "respond": "respond",
        }
    )

    # INNER LOOP EDGES (OBSERVE → THINK → PLAN → BUILD → VERIFY → LEARN → iterate)
    builder.add_edge("observe", "think")
    builder.add_edge("think", "plan")
    builder.add_edge("plan", "pick_next")  # Plan creates work items

    # Pick next → worktree → build (act)
    builder.add_conditional_edges(
        "pick_next",
        route_after_pick_next,
        {
            "worktree_prep": "worktree_prep",
            "respond": "respond",
        }
    )

    builder.add_edge("worktree_prep", "build")

    # After build: validate if needed, then execute (loop.spec.md: BUILD → EXECUTE)
    builder.add_conditional_edges(
        "build",
        route_after_act_build,
        {
            "validate": "validate",
            "execute": "execute",
        }
    )

    # After validate: register or execute
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "register": "register",
            "execute": "execute",
        }
    )

    # Register leads to execute (loop.spec.md: registration then execute)
    builder.add_edge("register", "execute")

    # After execute: verify (loop.spec.md: EXECUTE → VERIFY)
    builder.add_edge("execute", "verify")

    # After verify: learn
    builder.add_edge("verify", "learn")

    # After learn: check if should iterate or respond
    builder.add_conditional_edges(
        "learn",
        should_iterate,
        {
            "observe": "observe",  # Continue iterating
            "respond": "respond",  # Done iterating
        }
    )

    # OUTER LOOP CONTINUATION
    builder.add_edge("respond", "cycle_limit")

    # After cycle_limit: continue, idle, or end
    builder.add_conditional_edges(
        "cycle_limit",
        should_continue_outer,
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
