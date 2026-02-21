"""
Routing Module - Centralized Graph Routing Decisions.

All routing functions return typed Literal strings for LangGraph compatibility.

Note: We import ProgenitorState directly (not via TYPE_CHECKING) because
LangGraph's get_type_hints() evaluates type annotations at runtime for
conditional edge routing functions.

Routing functions:
  - route_after_decide: respond or pick_next
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from .state.helpers import get_actionable_items
from .state.models import ProgenitorState

logger = logging.getLogger(__name__)


# =============================================================================
# Chat Loop Routing
# =============================================================================

def route_after_decide(state: ProgenitorState) -> Literal["pick_next", "respond"]:
    """Route based on decision type.

    Routes to:
        - 'respond': For direct answers (includes build acknowledgments)
        - 'pick_next': For work items to execute (tool calls, agent calls, etc.)
    """
    from .utils import get_attr, get_enum_value

    decision = state.get("current_decision")

    if not decision:
        return "respond"

    action = get_attr(decision, "action", "answer_directly")
    action_value = get_enum_value(action, "answer_directly")

    if action_value in ("answer", "answer_directly"):
        return "respond"

    return "pick_next"


# =============================================================================
# Utility Functions
# =============================================================================

def has_pending_work(state: ProgenitorState) -> bool:
    """Check if there's actionable work in the backlog."""
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
        "report": verification.get("current_report"),
        "gaps": verification.get("gaps", []),
    }
