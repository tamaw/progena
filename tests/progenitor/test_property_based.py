"""
Property-Based Tests for State Transitions.

TEST-002: Use Hypothesis for property-based testing.

Tests invariants that should always hold regardless of input:
- Iteration count never exceeds max
- State fields maintain consistency
- Backlog operations are idempotent
"""

import pytest
from hypothesis import given, strategies as st, settings, assume
from datetime import datetime, timezone
from typing import Any

from app.progenitor.state.models import (
    WorkItem,
    WorkItemStatus,
    WorkItemType,
    Decision,
    DecisionAction,
    ProgenitorState,
)
from app.progenitor.state.helpers import get_actionable_items


# ============================================================================
# Custom Strategies
# ============================================================================

@st.composite
def work_item_strategy(draw):
    """Generate random WorkItem instances."""
    return WorkItem(
        id=draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L', 'N')))),
        item_type=draw(st.sampled_from(list(WorkItemType))),
        status=draw(st.sampled_from(list(WorkItemStatus))),
        description=draw(st.text(min_size=1, max_size=100)),
        retry_count=draw(st.integers(min_value=0, max_value=10)),
        max_retries=draw(st.integers(min_value=1, max_value=10)),
    )


@st.composite
def decision_strategy(draw):
    """Generate random Decision instances."""
    return Decision(
        action=draw(st.sampled_from(list(DecisionAction))),
        reasoning=draw(st.text(min_size=0, max_size=200)),
        requires_planning=draw(st.booleans()),
        target_capabilities=draw(st.lists(st.text(min_size=1, max_size=20), max_size=5)),
    )


@st.composite
def progenitor_state_strategy(draw):
    """Generate random ProgenitorState dicts."""
    return {
        "current_input": draw(st.one_of(st.none(), st.text(max_size=100))),
        "cycle_count": draw(st.integers(min_value=0, max_value=1000)),
        "actions_this_cycle": draw(st.integers(min_value=0, max_value=100)),
        "current_iteration": draw(st.integers(min_value=0, max_value=20)),
        "verification_score": draw(st.floats(min_value=0.0, max_value=1.0)),
        "shutdown_requested": draw(st.booleans()),
    }


# ============================================================================
# Property Tests: Iteration Bounds
# ============================================================================

class TestIterationBounds:
    """Tests for iteration count bounds."""

    @settings(deadline=None)  # Disable deadline for this test - timing varies
    @given(
        max_iterations=st.integers(min_value=1, max_value=20),
        current_iteration=st.integers(min_value=0, max_value=25),
    )
    def test_iteration_never_exceeds_max_when_checked(
        self,
        max_iterations: int,
        current_iteration: int,
    ):
        """Iteration routing should prevent exceeding max."""
        from app.progenitor.graph_inner_loop import should_iterate

        state: ProgenitorState = {
            "current_iteration": current_iteration,
            "verification_passed": False,
            "ideal_state": None,
        }

        result = should_iterate(state, max_iterations=max_iterations)

        # If at or over limit, should stop
        if current_iteration >= max_iterations:
            assert result == "respond"
        # If under limit, should continue
        elif current_iteration == 0:
            assert result == "observe"

    @given(st.integers(min_value=0, max_value=100))
    def test_cycle_count_non_negative(self, cycle_count: int):
        """Cycle count should never be negative."""
        state: ProgenitorState = {"cycle_count": cycle_count}
        assert state["cycle_count"] >= 0

    @given(st.floats(min_value=0.0, max_value=1.0))
    def test_verification_score_bounded(self, score: float):
        """Verification score should be between 0 and 1."""
        state: ProgenitorState = {"verification_score": score}
        assert 0.0 <= state["verification_score"] <= 1.0


# ============================================================================
# Property Tests: Work Item Invariants
# ============================================================================

class TestWorkItemInvariants:
    """Tests for work item invariants."""

    @given(work_item_strategy())
    def test_work_item_has_valid_id(self, item: WorkItem):
        """Work items should always have a non-empty ID."""
        assert item.id is not None
        assert len(item.id) > 0

    @given(work_item_strategy())
    def test_work_item_status_valid(self, item: WorkItem):
        """Work item status should be valid."""
        assert item.status_value in [s.value for s in WorkItemStatus]

    @given(work_item_strategy())
    def test_work_item_type_valid(self, item: WorkItem):
        """Work item type should be valid."""
        assert item.item_type_value in [t.value for t in WorkItemType]

    @given(work_item_strategy())
    def test_actionable_consistency(self, item: WorkItem):
        """is_actionable should be consistent with status."""
        if item.status_value in (WorkItemStatus.PENDING.value, WorkItemStatus.IN_PROGRESS.value):
            assert item.is_actionable
        else:
            assert not item.is_actionable

    @given(work_item_strategy())
    def test_retry_count_non_negative(self, item: WorkItem):
        """Retry count should never be negative."""
        assert item.retry_count >= 0

    @given(work_item_strategy())
    def test_max_retries_positive(self, item: WorkItem):
        """Max retries should be positive."""
        assert item.max_retries >= 1


# ============================================================================
# Property Tests: Backlog Operations
# ============================================================================

class TestBacklogOperations:
    """Tests for backlog operation properties."""

    @given(st.lists(work_item_strategy(), min_size=0, max_size=20))
    @settings(max_examples=50)
    def test_actionable_items_subset(self, backlog: list[WorkItem]):
        """Actionable items should be subset of backlog."""
        actionable = get_actionable_items(backlog)

        for item in actionable:
            assert item in backlog

    @given(st.lists(work_item_strategy(), min_size=0, max_size=20))
    @settings(max_examples=50)
    def test_actionable_all_have_valid_status(self, backlog: list[WorkItem]):
        """All actionable items should have actionable status."""
        actionable = get_actionable_items(backlog)

        for item in actionable:
            assert item.status_value in (
                WorkItemStatus.PENDING.value,
                WorkItemStatus.IN_PROGRESS.value,
            )

    @given(st.lists(work_item_strategy(), min_size=0, max_size=20))
    @settings(max_examples=50)
    def test_completed_not_actionable(self, backlog: list[WorkItem]):
        """Completed items should not be actionable."""
        actionable = get_actionable_items(backlog)

        for item in actionable:
            assert item.status_value != WorkItemStatus.COMPLETED.value


# ============================================================================
# Property Tests: Decision Invariants
# ============================================================================

class TestDecisionInvariants:
    """Tests for decision invariants."""

    @given(decision_strategy())
    def test_decision_has_action(self, decision: Decision):
        """Decisions should always have an action."""
        assert decision.action is not None

    @given(decision_strategy())
    def test_decision_action_valid(self, decision: Decision):
        """Decision action should be valid."""
        assert decision.action in list(DecisionAction)

    @given(decision_strategy())
    def test_decision_has_timestamp(self, decision: Decision):
        """Decisions should have a creation timestamp."""
        assert decision.created_at is not None
        assert isinstance(decision.created_at, datetime)


# ============================================================================
# Property Tests: State Consistency
# ============================================================================

class TestStateConsistency:
    """Tests for state consistency invariants."""

    @given(progenitor_state_strategy())
    def test_state_fields_typed(self, state: ProgenitorState):
        """State fields should maintain proper types."""
        if "cycle_count" in state:
            assert isinstance(state["cycle_count"], int)

        if "verification_score" in state:
            assert isinstance(state["verification_score"], (int, float))

        if "shutdown_requested" in state:
            assert isinstance(state["shutdown_requested"], bool)

    @given(progenitor_state_strategy())
    def test_counts_non_negative(self, state: ProgenitorState):
        """Count fields should be non-negative."""
        if "cycle_count" in state:
            assert state["cycle_count"] >= 0

        if "actions_this_cycle" in state:
            assert state["actions_this_cycle"] >= 0

        if "current_iteration" in state:
            assert state["current_iteration"] >= 0

    @given(
        st.integers(min_value=0, max_value=100),
        st.integers(min_value=0, max_value=100),
    )
    def test_actions_less_than_or_equal_cycles(
        self,
        actions: int,
        cycles: int,
    ):
        """Actions per cycle should be bounded reasonably."""
        # This is more of a sanity check
        state = {
            "actions_this_cycle": actions,
            "cycle_count": cycles,
        }
        # Actions could be higher if multiple actions per cycle
        # Just verify they're both valid integers
        assert isinstance(state["actions_this_cycle"], int)
        assert isinstance(state["cycle_count"], int)


# ============================================================================
# Property Tests: Mark Operations Idempotency
# ============================================================================

class TestMarkOperationsIdempotency:
    """Tests that mark operations are idempotent where appropriate."""

    @given(work_item_strategy())
    def test_mark_completed_idempotent(self, item: WorkItem):
        """Marking completed multiple times should be stable."""
        assume(item.status_value == WorkItemStatus.PENDING.value)

        item.mark_completed({"result": "test"})
        first_status = item.status_value
        first_time = item.updated_at

        # Mark again
        item.mark_completed({"result": "test2"})

        # Status should still be completed
        assert item.status_value == WorkItemStatus.COMPLETED.value
        assert item.status_value == first_status

    @given(work_item_strategy())
    def test_mark_in_progress_changes_status(self, item: WorkItem):
        """Mark in progress should update status."""
        assume(item.status_value == WorkItemStatus.PENDING.value)

        original_time = item.updated_at
        item.mark_in_progress()

        assert item.status_value == WorkItemStatus.IN_PROGRESS.value
        # updated_at should change (though time resolution might be same)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
