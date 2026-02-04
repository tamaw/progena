"""
Routing Module - Centralized Graph Routing Decisions.

Extracts common routing logic from inner and outer loops to reduce duplication.
All routing functions return typed Literal strings for LangGraph compatibility.

Note: We import ProgenitorState directly (not via TYPE_CHECKING) because
LangGraph's get_type_hints() evaluates type annotations at runtime for
conditional edge routing functions.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from .constants import (
    DEFAULT_MAX_INNER_ITERATIONS,
    DEFAULT_VERIFICATION_TARGET_SCORE,
    RouteTarget,
)
from .state.helpers import get_actionable_items
from .state.models import ProgenitorState
from .state.ideal_state import IdealState

logger = logging.getLogger(__name__)


# =============================================================================
# Inner Loop Routing
# =============================================================================

def ensure_ideal_state(ideal_state: Any) -> IdealState | None:
    """Convert ideal_state dict to IdealState object if needed."""
    from .state.ideal_state import IdealState

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
    return None


def should_iterate(
    state: ProgenitorState,
    max_iterations: int = DEFAULT_MAX_INNER_ITERATIONS
) -> Literal["observe", "respond"]:
    """Determine if inner loop should continue iterating.

    This is used for iteration control within the inner loop.
    Returns 'observe' to continue, 'respond' to stop.

    Checks stop conditions:
    - Clarification needed (interrupts to ask user)
    - Min score reached
    - Max iterations hit
    - Tool/time budget exhausted
    """
    iteration = state.get("current_iteration", 0)
    verification_report = state.get("last_verification_report")
    ideal_state = ensure_ideal_state(state.get("ideal_state"))

    # First iteration - always continue
    if iteration == 0:
        return "observe"

    # Check if clarification is needed
    if state.get("clarification_needed", False):
        logger.info("Clarification needed - interrupting inner loop")
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

        should_stop, reasons, _met_ids = ideal_state.check_stop_conditions(
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

    logger.info("Continuing to iteration %d", iteration + 1)
    return "observe"


def should_continue_inner_loop(state: ProgenitorState) -> Literal["observe", "done"]:
    """Determine if inner loop should continue or exit.

    Exit conditions (per spec):
    - Minimum verification score reached AND no more pending backlog items
    - Max iterations hit
    - Build cancelled/error
    
    Continue conditions:
    - Pending backlog items exist (process all items)
    - Verification score not yet met
    """
    # Check for cancellation
    if state.get("build_cancelled", False):
        logger.info("Build cancelled, exiting inner loop")
        return "done"

    # Check for error
    if state.get("build_error"):
        logger.info("Build error, exiting inner loop")
        return "done"

    # Check iteration limit
    current_iteration = state.get("current_iteration", 0)
    max_iterations = state.get("max_iterations", 10)

    if current_iteration >= max_iterations:
        logger.info("Max iterations (%d) reached, exiting inner loop", max_iterations)
        return "done"

    # Check for pending backlog items - process all items before exiting
    backlog = state.get("backlog", [])
    pending_items = _count_pending_backlog_items(backlog)
    
    if pending_items > 0:
        logger.info(
            "Found %d pending backlog items, continuing inner loop",
            pending_items
        )
        return "observe"

    # Check verification score (only exit if no pending items)
    verification_score = state.get("verification_score", 0.0)
    target_score = state.get("target_score", DEFAULT_VERIFICATION_TARGET_SCORE)

    if verification_score >= target_score:
        logger.info(
            "Verification score %.2f meets target %.2f, no pending items, exiting inner loop",
            verification_score, target_score
        )
        return "done"

    # Continue iterating (score not met, no pending items - may need new plan)
    logger.info(
        "Continuing inner loop: iteration=%d/%d, score=%.2f/%.2f, pending=%d",
        current_iteration, max_iterations, verification_score, target_score, pending_items
    )
    return "observe"


def _count_pending_backlog_items(backlog: list) -> int:
    """Count the number of pending (not completed) backlog items."""
    count = 0
    for item in backlog:
        if hasattr(item, "status"):
            status = item.status
            status_val = status.value if hasattr(status, "value") else str(status)
        elif isinstance(item, dict):
            status_val = item.get("status", "pending")
        else:
            continue
        
        if status_val in ("pending", "in_progress"):
            count += 1
    return count


def route_after_pick_next_inner(state: ProgenitorState) -> Literal["pick_next", "build"]:
    """Route after plan in inner loop - pick_next if backlog has items, else build directly.
    
    This allows the inner loop to process multiple backlog items created by the plan phase.
    """
    backlog = state.get("backlog", [])
    pending = _count_pending_backlog_items(backlog)
    
    if pending > 0:
        logger.debug("Inner loop: %d pending backlog items, routing to pick_next", pending)
        return "pick_next"
    
    # No backlog items - go directly to build (legacy behavior)
    logger.debug("Inner loop: No pending backlog items, routing to build")
    return "build"


def route_after_verify(state: ProgenitorState) -> Literal["learn", "done"]:
    """Route after verification - learn if continuing, done if complete."""
    verification_score = state.get("verification_score", 0.0)
    target_score = state.get("target_score", DEFAULT_VERIFICATION_TARGET_SCORE)
    current_iteration = state.get("current_iteration", 0)
    max_iterations = state.get("max_iterations", 10)

    # Stop conditions
    if state.get("build_cancelled", False):
        return "done"
    if state.get("build_error"):
        return "done"
    if verification_score >= target_score:
        return "done"
    if current_iteration >= max_iterations:
        return "done"

    # Continue to learn phase
    return "learn"


# =============================================================================
# Outer Loop Routing
# =============================================================================

def should_continue_outer_loop(state: ProgenitorState) -> Literal["pick_next", "idle", "end"]:
    """Determine if we should continue processing, idle, or shutdown.

    Returns:
        'pick_next': More work items to process
        'idle': No pending work, enter idle state
        'end': Shutdown requested or no pending work (allows loop runner to check input)
    """
    if state.get("shutdown_requested", False):
        logger.info("Shutdown requested, ending loop")
        return "end"

    backlog = state.get("backlog", [])
    pending = get_actionable_items(backlog)

    if pending:
        logger.debug("%d actionable items in backlog", len(pending))
        return "pick_next"

    # No pending work - end this graph cycle so the loop runner
    # can check for new user input
    logger.debug("No pending work, ending graph cycle to check for input")
    return "end"


def route_after_decide(state: ProgenitorState) -> Literal["pick_next", "respond"]:
    """Route based on decision type.

    Routes to:
        - 'respond': For direct answers (includes build acknowledgments)
        - 'pick_next': For work items to execute (tool calls, agent calls, etc.)

    No planner in outer loop - decide handles all decisions directly.
    Builds are started via BackgroundBuildRunner which uses the inner loop planner.
    """
    from .utils import get_attr, get_enum_value

    decision = state.get("current_decision")

    if not decision:
        return "respond"

    action = get_attr(decision, "action", "answer_directly")
    action_value = get_enum_value(action, "answer_directly")

    # Direct answers - respond immediately (includes build acknowledgments)
    if action_value in ("answer", "answer_directly"):
        return "respond"

    # Everything else goes to pick_next for execution
    return "pick_next"


def route_after_act(state: ProgenitorState) -> Literal["validate", "commit_core"]:
    """Route after action - validate if code was changed.

    Build operations (build_agent, build_mcp, upgrade) require validation
    before commit. Other operations skip directly to commit.
    """
    from .state.models import WorkItem

    current_item = state.get("current_work_item")

    if current_item:
        if isinstance(current_item, WorkItem):
            item_type = current_item.item_type_value
        elif isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
        else:
            item_type = ""

        if item_type in ("build_agent", "build_mcp", "upgrade"):
            return "validate"

    return "commit_core"


def route_after_validate(state: ProgenitorState) -> Literal["commit_core", "pick_next"]:
    """Route after validation.

    Successful validation proceeds to commit.
    Failed validation skips and picks next item.
    """
    validation_result = state.get("validation_result_v2")

    if validation_result and not validation_result.get("has_blocking_issues", True):
        logger.debug("Validation passed, proceeding to commit")
        return "commit_core"

    logger.warning("Validation failed, skipping to next item")
    return "pick_next"


def route_after_pick_next(state: ProgenitorState) -> Literal["worktree_prep", "respond"]:
    """Route after picking next item - skip to respond if no work."""
    current_item = state.get("current_work_item")

    if current_item is None:
        return "respond"

    return "worktree_prep"


# =============================================================================
# Utility Functions
# =============================================================================

def has_pending_work(state: ProgenitorState) -> bool:
    """Check if there's actionable work in backlog."""
    backlog = state.get("backlog", [])
    return len(get_actionable_items(backlog)) > 0


def is_build_in_progress(state: ProgenitorState) -> bool:
    """Check if a build is currently in progress."""
    return state.get("build_in_progress", False)


def should_stop_for_clarification(state: ProgenitorState) -> bool:
    """Check if we should stop and ask user for clarification."""
    return state.get("clarification_needed", False)


def get_iteration_info(state: ProgenitorState) -> dict[str, Any]:
    """Get current iteration information for logging/debugging."""
    return {
        "iteration": state.get("current_iteration", 0),
        "max_iterations": state.get("max_iterations", 10),
        "verification_score": state.get("verification_score", 0.0),
        "target_score": state.get("target_score", DEFAULT_VERIFICATION_TARGET_SCORE),
        "phase": state.get("build_phase"),
    }
