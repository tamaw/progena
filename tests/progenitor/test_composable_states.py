"""Tests for active composable state types."""

import unittest


class TestComposableSubstates(unittest.TestCase):
    def test_substate_imports(self):
        from app.progenitor.state import (
            ConversationState,
            LoopControlState,
            ProgenitorState,
            SchedulingState,
        )

        assert hasattr(ConversationState, "__annotations__")
        assert hasattr(SchedulingState, "__annotations__")
        assert hasattr(LoopControlState, "__annotations__")
        assert hasattr(ProgenitorState, "__annotations__")

    def test_conversation_state_fields(self):
        from app.progenitor.state import ConversationState

        annotations = ConversationState.__annotations__

        assert "messages" in annotations
        assert "current_input" in annotations
        assert "pending_response" in annotations

    def test_scheduling_state_fields(self):
        from app.progenitor.state import SchedulingState

        annotations = SchedulingState.__annotations__

        assert "schedule" in annotations
        assert "current_time" in annotations
        assert "due_items" in annotations
        assert "agents" in annotations

    def test_loop_control_state_fields(self):
        from app.progenitor.state import LoopControlState

        annotations = LoopControlState.__annotations__

        assert "cycle_count" in annotations
        assert "actions_this_cycle" in annotations
        assert "shutdown_requested" in annotations
        assert "thread_id" in annotations
        assert "last_cycle_response" in annotations

    def test_progenitor_state_inherits_active_substates(self):
        from app.progenitor.state import (
            ConversationState,
            LoopControlState,
            ProgenitorState,
            SchedulingState,
        )

        progenitor_annotations = ProgenitorState.__annotations__

        for substate in [
            ConversationState,
            SchedulingState,
            LoopControlState,
        ]:
            for field in substate.__annotations__:
                assert field in progenitor_annotations, (
                    f"ProgenitorState missing field {field} from {substate.__name__}"
                )

    def test_substate_dicts_are_valid_progenitor_state(self):
        from app.progenitor.state import ProgenitorState

        state: ProgenitorState = {
            "messages": [],
            "current_input": "Hello",
        }

        assert state["current_input"] == "Hello"

    def test_combined_substate_dicts(self):
        from app.progenitor.state import ProgenitorState

        state: ProgenitorState = {
            "messages": [],
            "current_input": "Ask the agent",
            "cycle_count": 1,
            "thread_id": "thread-123",
        }

        assert state["current_input"] == "Ask the agent"
        assert state["cycle_count"] == 1

    def test_total_false_allows_partial_updates(self):
        from app.progenitor.state import (
            ConversationState,
            LoopControlState,
            ProgenitorState,
            SchedulingState,
        )

        for state_type in [
            ConversationState,
            SchedulingState,
            LoopControlState,
            ProgenitorState,
        ]:
            required = getattr(state_type, "__required_keys__", set())
            assert len(required) == 0, f"{state_type.__name__} has required keys: {required}"


class TestSubstateUsagePatterns(unittest.TestCase):
    def test_node_return_partial_state(self):
        from app.progenitor.state import ProgenitorState

        def conversation_node(state: ProgenitorState) -> ProgenitorState:
            return {
                "pending_response": "Response generated",
            }

        result = conversation_node({})
        assert "pending_response" in result
        assert len(result) == 1

    def test_node_can_type_hint_specific_substate(self):
        from app.progenitor.state import ConversationState
        from app.progenitor.state.models import ConversationMessage

        def get_messages(state: ConversationState) -> list:
            return state.get("messages", [])

        messages = get_messages(
            {
                "messages": [ConversationMessage(id="m1", role="user", content="hello")],
                "current_input": "test",
            }
        )
        assert messages[0].content == "hello"


if __name__ == "__main__":
    unittest.main()
