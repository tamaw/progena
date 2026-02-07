"""Tests for composable state types."""

import unittest
from typing import get_type_hints


class TestComposableSubstates(unittest.TestCase):
    """Test composable substate structure."""

    def test_substate_imports(self):
        """Test that all substates can be imported."""
        from app.progenitor.state import (
            ConversationState,
            WorkState,
            SchedulingState,
            LoopControlState,
            InnerLoopState,
            ExecutionState,
            ProgenitorState,
        )

        # Verify they're TypedDicts
        assert hasattr(ConversationState, '__annotations__')
        assert hasattr(WorkState, '__annotations__')
        assert hasattr(SchedulingState, '__annotations__')
        assert hasattr(LoopControlState, '__annotations__')
        assert hasattr(InnerLoopState, '__annotations__')
        assert hasattr(ExecutionState, '__annotations__')
        assert hasattr(ProgenitorState, '__annotations__')

    def test_conversation_state_fields(self):
        """Test ConversationState has expected fields."""
        from app.progenitor.state import ConversationState

        annotations = ConversationState.__annotations__

        assert 'messages' in annotations
        assert 'current_input' in annotations
        assert 'pending_response' in annotations

    def test_work_state_fields(self):
        """Test WorkState has expected fields."""
        from app.progenitor.state import WorkState

        annotations = WorkState.__annotations__

        assert 'backlog' in annotations
        assert 'current_work_item' in annotations
        assert 'current_decision' in annotations

    def test_scheduling_state_fields(self):
        """Test SchedulingState has expected fields."""
        from app.progenitor.state import SchedulingState

        annotations = SchedulingState.__annotations__

        assert 'schedule' in annotations
        assert 'current_time' in annotations
        assert 'due_items' in annotations
        assert 'agents' in annotations

    def test_loop_control_state_fields(self):
        """Test LoopControlState has expected fields."""
        from app.progenitor.state import LoopControlState

        annotations = LoopControlState.__annotations__

        assert 'cycle_count' in annotations
        assert 'actions_this_cycle' in annotations
        assert 'shutdown_requested' in annotations
        assert 'run_id' in annotations

    def test_inner_loop_state_fields(self):
        """Test InnerLoopState has expected fields."""
        from app.progenitor.state import InnerLoopState

        annotations = InnerLoopState.__annotations__

        # Key inner loop fields - now nested
        assert 'iteration' in annotations  # Nested object with current, max, start_time
        assert 'phases' in annotations  # Contains verify.score, verify.passed
        assert 'history' in annotations  # Contains lessons_learned, artefacts
        assert 'best' in annotations  # Contains iteration, score, passed

    def test_execution_state_fields(self):
        """Test ExecutionState has expected fields."""
        from app.progenitor.state import ExecutionState

        annotations = ExecutionState.__annotations__

        # Execution results now in phases.execute
        assert 'pending_tool_calls' in annotations
        assert 'work_item_completed' in annotations

    def test_progenitor_state_inherits_all_substates(self):
        """Test ProgenitorState includes all fields from substates."""
        from app.progenitor.state import (
            ConversationState,
            WorkState,
            SchedulingState,
            LoopControlState,
            InnerLoopState,
            ExecutionState,
            ProgenitorState,
        )

        progenitor_annotations = ProgenitorState.__annotations__

        # Check it has fields from each substate
        for substate in [ConversationState, WorkState, SchedulingState,
                         LoopControlState, InnerLoopState, ExecutionState]:
            for field in substate.__annotations__:
                assert field in progenitor_annotations, (
                    f"ProgenitorState missing field {field} from {substate.__name__}"
                )

    def test_substate_dicts_are_valid_progenitor_state(self):
        """Test that individual substate dicts can be used as ProgenitorState."""
        from app.progenitor.state import ProgenitorState

        # Create partial state with only conversation fields
        state: ProgenitorState = {
            "messages": [],
            "current_input": "Hello",
        }

        assert state["current_input"] == "Hello"

    def test_combined_substate_dicts(self):
        """Test combining multiple substates."""
        from app.progenitor.state import ProgenitorState

        state: ProgenitorState = {
            # Conversation
            "messages": [],
            "current_input": "Build an agent",
            # Work
            "backlog": [],
            # Loop control
            "cycle_count": 1,
            "run_id": "test-123",
        }

        assert state["current_input"] == "Build an agent"
        assert state["cycle_count"] == 1

    def test_total_false_allows_partial_updates(self):
        """Test that all substates have total=False for partial updates."""
        from app.progenitor.state import (
            ConversationState,
            WorkState,
            SchedulingState,
            LoopControlState,
            InnerLoopState,
            ExecutionState,
            ProgenitorState,
        )

        # TypedDicts with total=False have __required_keys__ and __optional_keys__
        for state_type in [ConversationState, WorkState, SchedulingState,
                           LoopControlState, InnerLoopState, ExecutionState,
                           ProgenitorState]:
            # All keys should be optional (total=False)
            required = getattr(state_type, '__required_keys__', set())
            assert len(required) == 0, (
                f"{state_type.__name__} has required keys: {required}"
            )


class TestSubstateUsagePatterns(unittest.TestCase):
    """Test common usage patterns with substates."""

    def test_node_return_partial_state(self):
        """Test returning only the fields a node touches."""
        from app.progenitor.state import ProgenitorState

        # Simulate a node that only updates conversation state
        def conversation_node(state: ProgenitorState) -> ProgenitorState:
            # Only return conversation-related updates
            return {
                "pending_response": "Response generated",
            }

        result = conversation_node({})
        assert "pending_response" in result
        assert len(result) == 1  # Only touched one field

    def test_node_can_type_hint_specific_substate(self):
        """Test that nodes can type hint specific substates."""
        from app.progenitor.state import ConversationState
        from app.progenitor.state.models import ConversationMessage

        def get_messages(state: ConversationState) -> list:
            """Node can type hint specific substate for clarity."""
            return state.get("messages", [])

        # Should work with full state too (due to structural typing)
        messages = get_messages({
            "messages": [ConversationMessage(id="m1", role="user", content="hello")],
            "current_input": "test",
        })
        assert messages[0].content == "hello"


if __name__ == "__main__":
    unittest.main()
