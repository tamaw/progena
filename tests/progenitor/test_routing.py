"""
Tests for app/progenitor/routing.py

Verifies routing functions work correctly for graph navigation.
"""

import uuid
from datetime import datetime
from typing import cast

from app.progenitor.routing import (
    ensure_ideal_state,
    get_iteration_info,
    has_pending_work,
    is_build_in_progress,
    route_after_act,
    route_after_decide,
    route_after_pick_next,
    route_after_validate,
    route_after_verify,
    should_continue_inner_loop,
    should_continue_outer_loop,
    should_iterate,
    should_stop_for_clarification,
)
from app.progenitor.state.ideal_state import IdealState
from app.progenitor.state.models import ProgenitorState, WorkItem, WorkItemStatus, WorkItemType


def make_work_item(**kwargs) -> WorkItem:
    """Helper to create WorkItem with required fields."""
    defaults: dict = {
        "id": str(uuid.uuid4()),
        "item_type": WorkItemType.BUILD_AGENT,
        "description": "Test",
        "status": WorkItemStatus.PENDING,
        "context": {},
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "retry_count": 0,
        "max_retries": 3,
        "result": None,
    }
    defaults.update(kwargs)
    return WorkItem(**defaults)


def make_ideal_state(**kwargs) -> IdealState:
    """Helper to create IdealState with required fields."""
    defaults: dict = {
        "task_description": "Test task",
    }
    defaults.update(kwargs)
    return IdealState(**defaults)


def make_state(**kwargs) -> ProgenitorState:
    """Helper to create a ProgenitorState with given fields.

    Uses cast to satisfy Pylance while allowing partial state dicts
    that work correctly at runtime with TypedDict's structural typing.
    """
    return cast(ProgenitorState, kwargs)


class TestEnsureIdealState:
    """Tests for ensure_ideal_state helper function."""

    def test_returns_none_for_none_input(self):
        """Should return None when input is None."""
        result = ensure_ideal_state(None)
        assert result is None

    def test_returns_ideal_state_unchanged(self):
        """Should return IdealState objects unchanged."""
        ideal = make_ideal_state(task_description="Test")
        result = ensure_ideal_state(ideal)
        assert result is ideal

    def test_converts_valid_dict(self):
        """Should convert valid dict to IdealState."""
        data = {"task_description": "Test description"}
        result = ensure_ideal_state(data)
        assert isinstance(result, IdealState)
        assert result.task_description == "Test description"

    def test_returns_none_for_invalid_dict(self):
        """Should return None for invalid dict that can't be converted."""
        # Missing required fields
        data = {"invalid_field": "value"}
        result = ensure_ideal_state(data)
        assert result is None


class TestShouldIterate:
    """Tests for should_iterate routing function."""

    def test_first_iteration_continues(self):
        """First iteration (0) should always continue."""
        state = make_state(iteration={"current": 0})
        result = should_iterate(state)
        assert result == "observe"

    def test_clarification_needed_stops(self):
        """Should stop when clarification is needed."""
        state = make_state(
            iteration={"current": 1},
            clarification={"needed": True},
        )
        result = should_iterate(state)
        assert result == "respond"

    def test_verification_passed_stops(self):
        """Should stop when verification passed."""
        state = make_state(
            iteration={"current": 1},
            verification={"current_passed": True},
        )
        result = should_iterate(state)
        assert result == "respond"

    def test_max_iterations_stops(self):
        """Should stop at max iterations."""
        state = make_state(iteration={"current": 5})
        result = should_iterate(state, max_iterations=5)
        assert result == "respond"

    def test_custom_max_iterations(self):
        """Should respect custom max_iterations parameter."""
        state = make_state(iteration={"current": 3})
        # Under custom limit - continue
        result = should_iterate(state, max_iterations=10)
        assert result == "observe"

        # At custom limit - stop
        result = should_iterate(state, max_iterations=3)
        assert result == "respond"

    def test_continues_under_limit(self):
        """Should continue when under max iterations."""
        state = make_state(iteration={"current": 2})
        result = should_iterate(state, max_iterations=5)
        assert result == "observe"


class TestShouldContinueInnerLoop:
    """Tests for should_continue_inner_loop routing function."""

    def test_cancelled_build_exits(self):
        """Should exit when build is cancelled."""
        state = make_state(build_cancelled=True)
        result = should_continue_inner_loop(state)
        assert result == "done"

    def test_build_error_exits(self):
        """Should exit on build error."""
        state = make_state(build_error="Some error occurred")
        result = should_continue_inner_loop(state)
        assert result == "done"

    def test_max_iterations_exits(self):
        """Should exit at max iterations."""
        state = make_state(
            iteration={"current": 10, "max": 10},
        )
        result = should_continue_inner_loop(state)
        assert result == "done"

    def test_target_score_reached_exits(self):
        """Should exit when verification score reaches target."""
        state = make_state(
            verification={"current_score": 0.95, "target_score": 0.95},
            iteration={"current": 1, "max": 10},
        )
        result = should_continue_inner_loop(state)
        assert result == "done"

    def test_continues_when_under_limits(self):
        """Should continue when under all limits."""
        state = make_state(
            verification={"current_score": 0.5, "target_score": 0.95},
            iteration={"current": 2, "max": 10},
        )
        result = should_continue_inner_loop(state)
        assert result == "observe"


class TestRouteAfterVerify:
    """Tests for route_after_verify routing function."""

    def test_cancelled_returns_done(self):
        """Should return done when cancelled."""
        state = make_state(build_cancelled=True)
        result = route_after_verify(state)
        assert result == "done"

    def test_error_returns_done(self):
        """Should return done on error."""
        state = make_state(build_error="error")
        result = route_after_verify(state)
        assert result == "done"

    def test_score_met_returns_done(self):
        """Should return done when score met."""
        state = make_state(
            verification={"current_score": 0.96, "target_score": 0.95},
        )
        result = route_after_verify(state)
        assert result == "done"

    def test_max_iterations_returns_done(self):
        """Should return done at max iterations."""
        state = make_state(
            iteration={"current": 10, "max": 10},
        )
        result = route_after_verify(state)
        assert result == "done"

    def test_continues_to_learn(self):
        """Should continue to learn when not done."""
        state = make_state(
            verification={"current_score": 0.5, "target_score": 0.95},
            iteration={"current": 2, "max": 10},
        )
        result = route_after_verify(state)
        assert result == "learn"


class TestShouldContinueOuterLoop:
    """Tests for should_continue_outer_loop routing function."""

    def test_shutdown_requested_ends(self):
        """Should end when shutdown requested."""
        state = make_state(shutdown_requested=True)
        result = should_continue_outer_loop(state)
        assert result == "end"

    def test_pending_work_picks_next(self):
        """Should pick_next when there's pending work."""
        state = make_state(
            backlog=[{"status": "pending", "id": "1"}],
        )
        result = should_continue_outer_loop(state)
        assert result == "pick_next"

    def test_empty_backlog_ends(self):
        """Should end when backlog is empty."""
        state = make_state(backlog=[])
        result = should_continue_outer_loop(state)
        assert result == "end"


class TestRouteAfterDecide:
    """Tests for route_after_decide routing function."""

    def test_no_decision_responds(self):
        """Should respond when no decision."""
        state = make_state(current_decision=None)
        result = route_after_decide(state)
        assert result == "respond"

    def test_answer_action_responds(self):
        """Should respond for answer action."""
        state = make_state(
            current_decision={"action": "answer"},
        )
        result = route_after_decide(state)
        assert result == "respond"

    def test_answer_directly_responds(self):
        """Should respond for answer_directly action."""
        state = make_state(
            current_decision={"action": "answer_directly"},
        )
        result = route_after_decide(state)
        assert result == "respond"

    def test_build_action_picks_next(self):
        """Should pick_next for build action."""
        state = make_state(
            current_decision={"action": "build_agent"},
        )
        result = route_after_decide(state)
        assert result == "pick_next"


class TestRouteAfterAct:
    """Tests for route_after_act routing function."""

    def test_build_agent_validates(self):
        """Should validate for build_agent items."""
        state = make_state(
            current_work_item=make_work_item(item_type=WorkItemType.BUILD_AGENT, description="test"),
        )
        result = route_after_act(state)
        assert result == "validate"

    def test_build_tool_validates(self):
        """Should validate for build_tool items."""
        state = make_state(
            current_work_item=make_work_item(item_type=WorkItemType.BUILD_TOOL, description="test"),
        )
        result = route_after_act(state)
        assert result == "validate"

    def test_upgrade_validates(self):
        """Should validate for upgrade items."""
        state = make_state(
            current_work_item=make_work_item(item_type=WorkItemType.UPGRADE, description="test"),
        )
        result = route_after_act(state)
        assert result == "validate"

    def test_other_types_commit(self):
        """Should commit for other item types."""
        state = make_state(
            current_work_item=make_work_item(item_type=WorkItemType.SYSTEM_TOOL, description="test"),
        )
        result = route_after_act(state)
        assert result == "commit_core"

    def test_dict_work_item_build_validates(self):
        """Should handle dict work items for builds."""
        state = make_state(
            current_work_item={"item_type": "build_agent", "description": "test"},
        )
        result = route_after_act(state)
        assert result == "validate"

    def test_no_work_item_commits(self):
        """Should commit when no work item."""
        state = make_state(current_work_item=None)
        result = route_after_act(state)
        assert result == "commit_core"


class TestRouteAfterValidate:
    """Tests for route_after_validate routing function."""

    def test_validation_passed_commits(self):
        """Should commit when validation passed."""
        state = make_state(
            validation_result_v2={"has_blocking_issues": False},
        )
        result = route_after_validate(state)
        assert result == "commit_core"

    def test_validation_failed_picks_next(self):
        """Should pick_next when validation failed."""
        state = make_state(
            validation_result_v2={"has_blocking_issues": True},
        )
        result = route_after_validate(state)
        assert result == "pick_next"

    def test_no_validation_result_picks_next(self):
        """Should pick_next when no validation result."""
        state = make_state(validation_result_v2=None)
        result = route_after_validate(state)
        assert result == "pick_next"


class TestRouteAfterPickNext:
    """Tests for route_after_pick_next routing function."""

    def test_no_item_responds(self):
        """Should respond when no work item."""
        state = make_state(current_work_item=None)
        result = route_after_pick_next(state)
        assert result == "respond"

    def test_with_item_preps_worktree(self):
        """Should prep worktree when work item exists."""
        state = make_state(
            current_work_item=make_work_item(item_type=WorkItemType.BUILD_AGENT, description="test"),
        )
        result = route_after_pick_next(state)
        assert result == "worktree_prep"


class TestUtilityFunctions:
    """Tests for utility functions in routing module."""

    def test_has_pending_work_with_pending(self):
        """Should return True when pending work exists."""
        state = make_state(backlog=[{"status": "pending", "id": "1"}])
        assert has_pending_work(state) is True

    def test_has_pending_work_without_pending(self):
        """Should return False when no pending work."""
        state = make_state(backlog=[])
        assert has_pending_work(state) is False

    def test_is_build_in_progress_true(self):
        """Should return True when build in progress."""
        state = make_state(build_in_progress=True)
        assert is_build_in_progress(state) is True

    def test_is_build_in_progress_false(self):
        """Should return False when no build in progress."""
        state = make_state(build_in_progress=False)
        assert is_build_in_progress(state) is False

    def test_should_stop_for_clarification_true(self):
        """Should return True when clarification needed."""
        state = make_state(clarification={"needed": True})
        assert should_stop_for_clarification(state) is True

    def test_should_stop_for_clarification_false(self):
        """Should return False when no clarification needed."""
        state = make_state(clarification={"needed": False})
        assert should_stop_for_clarification(state) is False

    def test_get_iteration_info(self):
        """Should return correct iteration info dict."""
        state = make_state(
            iteration={"current": 3, "max": 10},
            verification={"current_score": 0.75, "target_score": 0.95},
            build_phase="execute",
        )
        info = get_iteration_info(state)
        assert info["iteration"] == 3
        assert info["max_iterations"] == 10
        assert info["verification_score"] == 0.75
        assert info["target_score"] == 0.95
        assert info["phase"] == "execute"

class TestNestedStateKeyCompatibility:
    """Tests for nested vs flat state key compatibility.

    The inner loop uses nested keys (iteration.current, verification.current_score)
    while routing functions need to read both flat and nested keys.
    """

    def test_should_continue_inner_loop_with_nested_iteration(self):
        """Should read iteration from nested iteration.current."""
        state = make_state(
            iteration={"current": 5, "max": 10},
        )
        result = should_continue_inner_loop(state)
        # Should continue since iteration 5 < max 10
        assert result == "observe"

    def test_should_continue_inner_loop_with_nested_max_iterations(self):
        """Should read max_iterations from nested iteration.max."""
        state = make_state(
            iteration={"current": 10, "max": 10},
        )
        result = should_continue_inner_loop(state)
        assert result == "done"  # At max iterations

    def test_should_continue_inner_loop_with_nested_verification(self):
        """Should read verification_score from verification.current_score."""
        state = make_state(
            verification={"current_score": 0.95, "target_score": 0.8},
            iteration={"current": 1, "max": 10},
            backlog=[],  # No pending items
        )
        result = should_continue_inner_loop(state)
        assert result == "done"  # Score 0.95 >= target 0.8

    def test_route_after_verify_with_nested_keys(self):
        """Should read all values from nested structures."""
        state = make_state(
            verification={"current_score": 0.5, "target_score": 0.8},
            iteration={"current": 2, "max": 10},
        )
        result = route_after_verify(state)
        assert result == "learn"  # Score not met, continue

    def test_route_after_verify_nested_score_met(self):
        """Should return done when nested verification.current_score meets target."""
        state = make_state(
            verification={"current_score": 0.9, "target_score": 0.8},
            iteration={"current": 2, "max": 10},
        )
        result = route_after_verify(state)
        assert result == "done"

    def test_nested_verification_score_determines_routing(self):
        """Nested verification.current_score determines routing decision."""
        state = make_state(
            iteration={"current": 2, "max": 10},
            verification={"current_score": 0.95, "target_score": 0.8},  # Score meets target
        )
        result = route_after_verify(state)
        # Should use verification.current_score (0.95 >= 0.8), so done
        assert result == "done"
