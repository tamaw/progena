"""
Routing Module - Centralized Graph Routing Decisions.

All routing functions return typed Literal strings for LangGraph compatibility.

Note: We import ProgenitorState directly (not via TYPE_CHECKING) because
LangGraph's get_type_hints() evaluates type annotations at runtime for
conditional edge routing functions.

Routing functions by graph:
  Build loop (BackgroundBuildRunner / graph_build_loop):
    - should_continue_inner_loop  : continue iterating or exit build loop
    - route_after_verify          : learn phase or done
    - should_iterate              : deprecated iteration-level check

  Background graph (graph_background_loop):
    - route_after_decide          : respond or pick_next
    - route_after_act             : validate or commit_core
    - route_after_validate        : commit_core or pick_next
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from .config import DEFAULT_MAX_INNER_ITERATIONS, DEFAULT_VERIFICATION_TARGET_SCORE
from .state.execution_schemas import VerificationStatus
from .state.helpers import get_actionable_items, get_ideal_state
from .state.ideal_state import IdealState
from .state.models import BuildPhase, ProgenitorState

logger = logging.getLogger(__name__)


# =============================================================================
# Inner Loop Routing
# =============================================================================

# Keep these defaults aligned with RuntimeConfig.from_env() keys.
_DEFAULT_MAX_INNER_ITERATIONS = DEFAULT_MAX_INNER_ITERATIONS
_DEFAULT_TARGET_SCORE = DEFAULT_VERIFICATION_TARGET_SCORE


def _state_section(state: ProgenitorState, key: str) -> dict[str, Any]:
    """Return a dict-like section from state or an empty mapping."""
    value = state.get(key, {})
    if isinstance(value, dict):
        return value
    if value is not None:
        logger.warning("Expected state[%s] to be a dict, got %s", key, type(value).__name__)
    return {}


def _coerce_int(value: Any, default: int, *, label: str) -> int:
    """Parse an integer while preserving explicit zero values."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid %s %r; using default %d", label, value, default)
        return default


def _coerce_float(value: Any, default: float, *, label: str) -> float:
    """Parse a float while preserving explicit zero values."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Invalid %s %r; using default %.2f", label, value, default)
        return default


def _verification_status(state: ProgenitorState) -> str | None:
    """Return the explicit verification status when available."""
    verification = _state_section(state, "verification")
    status = verification.get("current_status")
    if isinstance(status, str) and status in {status.value for status in VerificationStatus}:
        return status

    report = verification.get("current_report")
    if isinstance(report, dict):
        report_status = report.get("status")
        if isinstance(report_status, str) and report_status in {
            status.value for status in VerificationStatus
        }:
            return report_status
    elif report is not None:
        report_status = getattr(report, "status", None)
        if isinstance(report_status, str) and report_status in {
            status.value for status in VerificationStatus
        }:
            return report_status
        if isinstance(report_status, VerificationStatus):
            return report_status.value

    if verification.get("current_passed", False):
        return VerificationStatus.PASSED.value
    return None


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
    state: ProgenitorState, max_iterations: int = _DEFAULT_MAX_INNER_ITERATIONS
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
    iter_info = _state_section(state, "iteration")
    iteration = _coerce_int(iter_info.get("current"), 0, label="iteration.current")
    verification = _state_section(state, "verification")
    ideal_state = ensure_ideal_state(get_ideal_state(state))

    # First iteration - always continue
    if iteration == 0:
        return "observe"

    # Check if clarification is needed
    clarification = _state_section(state, "clarification")
    if clarification.get("needed", False):
        logger.info("Clarification needed - interrupting inner loop")
        return "respond"

    # Check if verification passed
    status = _verification_status(state)
    if status == VerificationStatus.NOT_RUN.value:
        logger.warning("Verification did not run - interrupting inner loop")
        return "respond"
    if status == VerificationStatus.PASSED.value or verification.get("current_passed", False):
        logger.info("Verification passed at iteration %d", iteration)
        return "respond"

    # Check stop conditions from ideal state
    if ideal_state and verification.get("current_report"):
        verification_score = _coerce_float(
            verification.get("current_score"),
            0.0,
            label="verification.current_score",
        )
        actions_count = state.get("actions_this_cycle", 0)
        iteration_start = iter_info.get("start_time", time.time())
        elapsed_time = time.time() - iteration_start

        should_stop, reasons, _met_ids = ideal_state.check_stop_conditions(
            current_score=verification_score,
            iteration=iteration,
            elapsed_time=elapsed_time,
            tool_calls=actions_count,
        )

        if should_stop:
            logger.info("Stop conditions met: %s", ", ".join(reasons))
            return "respond"

    # Default: Continue iterating if under failsafe limit
    if iteration >= max_iterations:
        logger.warning("Max failsafe iterations (%d) reached", max_iterations)
        return "respond"

    logger.info("Continuing to iteration %d", iteration + 1)
    return "observe"


def _check_build_stop(
    state: ProgenitorState,
    context: str = "inner loop",
) -> tuple[bool, str]:
    """Check shared stop conditions for the inner build loop.

    Returns (should_stop, reason). Used by both should_continue_inner_loop
    and route_after_verify to avoid duplicate logic.
    """
    if state.get("build_cancelled", False):
        return True, "build cancelled"

    if state.get("build_error"):
        return True, f"build error: {state.get('build_error')}"

    iter_info = _state_section(state, "iteration")
    current_iteration = _coerce_int(iter_info.get("current"), 0, label="iteration.current")
    max_iterations = _coerce_int(
        iter_info.get("max"),
        _DEFAULT_MAX_INNER_ITERATIONS,
        label="iteration.max",
    )
    if current_iteration >= max_iterations:
        return True, f"max iterations ({max_iterations}) reached"

    verification = _state_section(state, "verification")
    verification_status = _verification_status(state)
    verification_score = _coerce_float(
        verification.get("current_score"),
        0.0,
        label="verification.current_score",
    )
    target_score = _coerce_float(
        verification.get("target_score"),
        _DEFAULT_TARGET_SCORE,
        label="verification.target_score",
    )
    if verification_status == VerificationStatus.NOT_RUN.value:
        return True, "verification not run"
    if verification_status == VerificationStatus.PASSED.value:
        return True, f"score {verification_score:.2f} meets target {target_score:.2f}"
    if verification_score >= target_score:
        return True, f"score {verification_score:.2f} meets target {target_score:.2f}"

    return False, ""


def should_continue_inner_loop(state: ProgenitorState) -> Literal["observe", "done"]:
    """Determine if the inner build loop should continue or exit.

    The inner loop (BackgroundBuildRunner) operates without a backlog —
    it runs directly against its own build_state. Stop conditions are:
    - Build cancelled or errored
    - Max iterations reached
    - Verification score meets target
    """
    should_stop, reason = _check_build_stop(state)
    if should_stop:
        logger.info("Exiting inner loop: %s", reason)
        return "done"

    iter_info = _state_section(state, "iteration")
    current_iteration = _coerce_int(iter_info.get("current"), 0, label="iteration.current")
    max_iterations = _coerce_int(
        iter_info.get("max"),
        _DEFAULT_MAX_INNER_ITERATIONS,
        label="iteration.max",
    )
    verification = _state_section(state, "verification")
    verification_score = _coerce_float(
        verification.get("current_score"),
        0.0,
        label="verification.current_score",
    )
    target_score = _coerce_float(
        verification.get("target_score"),
        _DEFAULT_TARGET_SCORE,
        label="verification.target_score",
    )
    logger.info(
        "Continuing inner loop: iteration=%d/%d, score=%.2f/%.2f",
        current_iteration,
        max_iterations,
        verification_score,
        target_score,
    )
    return "observe"


def route_after_verify(state: ProgenitorState) -> Literal["learn", "publish", "done"]:
    """Route after verify.

    The inner loop has three outcomes after verification:
      - done    : cancelled/errored (hard stop)
      - publish : verification met OR max iterations reached (best-effort PR)
      - learn   : continue iterating
    """
    if state.get("build_cancelled", False):
        logger.info("route_after_verify → done: build cancelled")
        return "done"

    if state.get("build_error"):
        logger.info("route_after_verify → done: build error: %s", state.get("build_error"))
        return "done"

    iter_info = _state_section(state, "iteration")
    current_iteration = _coerce_int(iter_info.get("current"), 0, label="iteration.current")
    max_iterations = _coerce_int(
        iter_info.get("max"),
        _DEFAULT_MAX_INNER_ITERATIONS,
        label="iteration.max",
    )

    verification = _state_section(state, "verification")
    verification_status = _verification_status(state)
    verification_score = _coerce_float(
        verification.get("current_score"),
        0.0,
        label="verification.current_score",
    )
    target_score = _coerce_float(
        verification.get("target_score"),
        _DEFAULT_TARGET_SCORE,
        label="verification.target_score",
    )

    if verification_status == VerificationStatus.NOT_RUN.value:
        logger.warning("route_after_verify → done: verification did not run")
        return "done"

    if current_iteration >= max_iterations:
        logger.info("route_after_verify → publish: max iterations (%d) reached", max_iterations)
        return "publish"

    if verification_status == VerificationStatus.PASSED.value or verification_score >= target_score:
        logger.info(
            "route_after_verify → publish: score %.2f meets target %.2f",
            verification_score,
            target_score,
        )
        return "publish"

    logger.info(
        "route_after_verify → learn: iteration=%d/%d, score=%.2f/%.2f",
        current_iteration,
        max_iterations,
        verification_score,
        target_score,
    )
    return "learn"


# =============================================================================
# Background Graph Routing
# =============================================================================


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

    Build operations (build_agent, build_mcp, build_progenitor, upgrade) require validation
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

        if item_type in ("build_agent", "build_mcp", "build_progenitor", "upgrade"):
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


# =============================================================================
# Utility Functions
# =============================================================================


def has_pending_work(state: ProgenitorState) -> bool:
    """Check if there's actionable work in the backlog (used by background graph)."""
    backlog = state.get("backlog", [])
    return len(get_actionable_items(backlog)) > 0


def is_build_in_progress(state: ProgenitorState) -> bool:
    """Check if a build is currently in progress."""
    return state.get("build_in_progress", False)


def should_stop_for_clarification(state: ProgenitorState) -> bool:
    """Check if we should stop and ask user for clarification."""
    clarification = state.get("clarification", {})
    return clarification.get("needed", False)


def get_verification_state(state: ProgenitorState) -> dict[str, Any]:
    """Get verification state from the consolidated verification field."""
    verification = state.get("verification", {})
    return {
        "score": verification.get("current_score", 0.0),
        "passed": verification.get("current_passed", False),
        "target": verification.get("target_score", _DEFAULT_TARGET_SCORE),
        "report": verification.get("current_report"),
        "gaps": verification.get("gaps", []),
    }


def get_iteration_info(state: ProgenitorState) -> dict[str, Any]:
    """Get current iteration information for logging/debugging."""
    iter_info = state.get("iteration", {})
    verification = get_verification_state(state)

    current = iter_info.get("current", 0)
    max_iter = iter_info.get("max", _DEFAULT_MAX_INNER_ITERATIONS)

    # Get build phase - use enum value if possible
    build_phase = state.get("build_phase")
    if build_phase and hasattr(BuildPhase, build_phase.upper()):
        build_phase = BuildPhase(build_phase).value

    return {
        "iteration": current,
        "max_iterations": max_iter,
        "verification_score": verification["score"],
        "target_score": verification["target"],
        "verification_passed": verification["passed"],
        "phase": build_phase,
        "gaps": verification["gaps"],
    }
