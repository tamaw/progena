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
from .state.helpers import get_actionable_items
from .state.ideal_state import IdealState
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

# Import utilities
from .utils import get_attr, get_enum_value

logger = logging.getLogger(__name__)


# Default failsafe - can be overridden via config passed to create_progenitor_graph
DEFAULT_MAX_INNER_ITERATIONS = 5


def _ensure_ideal_state(ideal_state: Any) -> IdealState | None:
    """Convert ideal_state dict to IdealState object if needed.

    LangGraph state may serialize IdealState to dict when passing through nodes.
    This helper ensures we always have an IdealState object for methods.
    """
    if ideal_state is None:
        return None
    if isinstance(ideal_state, IdealState):
        return ideal_state
    if isinstance(ideal_state, dict):
        try:
            return IdealState(**ideal_state)
        except Exception as e:
            logger.warning("Failed to convert ideal_state dict to IdealState: %s", e)
            return None
    logger.warning("Unknown ideal_state type: %s", type(ideal_state))
    return None


def should_iterate(state: ProgenitorState, max_iterations: int = DEFAULT_MAX_INNER_ITERATIONS) -> Literal["observe", "respond"]:
    """
    Determine if inner loop should continue iterating.

    IMPORTANT: This is a routing function - it must NOT mutate state.
    State updates happen in nodes, not in routing conditionals.

    Checks stop conditions:
    - Clarification needed (interrupts to ask user)
    - Min score reached
    - Max iterations hit
    - Tool/time budget exhausted

    Returns:
        'observe': Continue iterating (next iteration)
        'respond': Stop and respond (verification passed, clarification needed, or limits hit)
    """
    iteration = state.get("current_iteration", 0)
    verification_report = state.get("last_verification_report")
    ideal_state_raw = state.get("ideal_state")
    ideal_state = _ensure_ideal_state(ideal_state_raw)

    # First iteration - always continue
    if iteration == 0:
        return "observe"

    # Check if clarification is needed - this interrupts the loop
    # to ask the user for more information
    if state.get("clarification_needed", False):
        logger.info("Clarification needed - interrupting inner loop to ask user")
        return "respond"

    # Check if verification passed
    if state.get("verification_passed", False):
        logger.info("Verification passed at iteration %d", iteration)
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
            logger.info("Stop conditions met: %s", ', '.join(reasons))
            return "respond"

    # Default: Continue iterating if under failsafe limit
    if iteration >= max_iterations:
        logger.warning("Max failsafe iterations (%d) reached", max_iterations)
        return "respond"

    # Continue iterating
    logger.info("Continuing to iteration %d", iteration + 1)
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
    pending = get_actionable_items(backlog)

    if pending:
        logger.debug("%d actionable items in backlog", len(pending))
        return "pick_next"

    return "idle"


def route_after_decide(state: ProgenitorState) -> Literal["observe", "pick_next", "respond"]:
    """
    Route based on decision type.

    For complex tasks requiring iteration:
        → 'observe' (enter inner loop)
    For simple tasks:
        → 'pick_next' or 'respond'
    For clarification responses:
        → 'observe' (resume inner loop with user context)

    NOTE: This is a pure routing function. State initialization for the inner
    loop happens in the 'observe' node, not here.
    """
    decision = state.get("current_decision")

    # Check if this is a clarification response - resume inner loop
    # When user responds to a clarification request, we have:
    # - awaiting_clarification was True (now should be processed)
    # - clarification_response contains user's answer
    # - ideal_state still exists from the interrupted build
    if state.get("awaiting_clarification") and state.get("clarification_response"):
        logger.info("Clarification response received - resuming inner loop")
        return "observe"

    # Also check if we have an existing ideal_state and were waiting
    # This handles the case where intake processed the clarification
    if state.get("ideal_state") and state.get("clarification_response"):
        logger.info("Resuming inner loop with clarification context")
        return "observe"

    if not decision:
        return "respond"

    # Use helper for uniform object/dict access
    action = get_attr(decision, "action", "answer_directly")
    requires_planning = get_attr(decision, "requires_planning", False)

    # Get action value if it's an enum
    action_value = get_enum_value(action, "answer_directly")

    # Direct answers bypass inner loop
    if action_value in ("answer", "answer_directly"):
        return "respond"

    # Complex tasks enter inner loop with OBSERVE
    if requires_planning or action_value in ("build_agent", "build_mcp", "upgrade"):
        logger.info("Routing to inner loop (OBSERVE phase)")
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
    # Get max iterations from config (or use default)
    max_inner_iterations = getattr(config.runtime, 'max_inner_iterations', DEFAULT_MAX_INNER_ITERATIONS)

    # Create a bound version of should_iterate with the configured max iterations
    def should_iterate_configured(state: ProgenitorState) -> Literal["observe", "respond"]:
        """Configured version of should_iterate using runtime settings."""
        return should_iterate(state, max_iterations=max_inner_iterations)

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
        "register": register.create_node(tool_registry, state_manager, config),
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

    # After learn: check if should iterate or respond (using configured max iterations)
    builder.add_conditional_edges(
        "learn",
        should_iterate_configured,
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

    logger.info(
        "Created dual-loop graph with max_inner_iterations=%d",
        max_inner_iterations
    )

    return graph
