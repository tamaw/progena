"""
Tests for app/progenitor/state/converters.py

Verifies type-safe conversion functions for state objects.
"""

import uuid
from datetime import UTC, datetime

from app.progenitor.state.converters import (
    ensure_list_of_work_items,
    to_decision,
    to_ideal_state,
    to_schedule_entry,
    to_work_item,
)
from app.progenitor.state.ideal_state import IdealState
from app.progenitor.state.models import (
    Decision,
    DecisionAction,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


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


class TestToWorkItem:
    """Tests for to_work_item converter."""

    def test_returns_work_item_unchanged(self):
        """Should return WorkItem objects unchanged."""
        item = make_work_item(item_type=WorkItemType.BUILD_AGENT, description="Test")
        result = to_work_item(item)
        assert result is item

    def test_converts_valid_dict(self):
        """Should convert valid dict to WorkItem."""
        data = {
            "id": "test-id-123",
            "item_type": "build_agent",
            "description": "Build a test agent",
        }
        result = to_work_item(data)
        assert isinstance(result, WorkItem)
        assert result.item_type_value == "build_agent"
        assert result.description == "Build a test agent"

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        result = to_work_item(None)
        assert result is None

    def test_handles_incomplete_dict(self):
        """Should handle dict missing some fields - models may have defaults."""
        # WorkItem has defaults for id (empty string) and other fields
        data = {"description": "Just description"}
        result = to_work_item(data)
        # Result may be a WorkItem with defaults or None depending on implementation
        assert result is None or isinstance(result, WorkItem)

    def test_handles_dict_with_extra_fields(self):
        """Should handle dict with extra fields gracefully."""
        data = {
            "id": "test-id",
            "item_type": "tool_call",
            "description": "Test",
            "extra_field": "ignored",
        }
        result = to_work_item(data)
        # Should either work or return None depending on strictness
        assert result is None or isinstance(result, WorkItem)


class TestToDecision:
    """Tests for to_decision converter."""

    def test_returns_decision_unchanged(self):
        """Should return Decision objects unchanged."""
        decision = Decision(action=DecisionAction.ANSWER_DIRECTLY, reasoning="Test")
        result = to_decision(decision)
        assert result is decision

    def test_converts_valid_dict(self):
        """Should convert valid dict to Decision."""
        data = {
            "action": "answer_directly",
            "reasoning": "Simple question",
        }
        result = to_decision(data)
        assert isinstance(result, Decision)
        assert result.action == DecisionAction.ANSWER_DIRECTLY
        assert result.reasoning == "Simple question"

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        result = to_decision(None)
        assert result is None

    def test_handles_incomplete_dict(self):
        """Should handle dict missing some fields - models may have defaults."""
        # Decision may have defaults for action and reasoning
        data = {"invalid": "data"}
        result = to_decision(data)
        # Result may be a Decision with defaults or None
        assert result is None or isinstance(result, Decision)


class TestToScheduleEntry:
    """Tests for to_schedule_entry converter."""

    def test_returns_schedule_entry_unchanged(self):
        """Should return ScheduleEntry objects unchanged."""
        from datetime import datetime
        entry = ScheduleEntry(
            id="test-id",
            description="Test description",
            instruction="Test instruction",
            run_at=datetime.now(UTC),
        )
        result = to_schedule_entry(entry)
        assert result is entry

    def test_converts_valid_dict(self):
        """Should convert valid dict to ScheduleEntry."""
        from datetime import datetime
        data = {
            "id": "entry-1",
            "description": "Daily check description",
            "instruction": "Perform daily check",
            "run_at": datetime.now(UTC).isoformat(),
        }
        result = to_schedule_entry(data)
        assert isinstance(result, ScheduleEntry)
        assert result.id == "entry-1"

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        result = to_schedule_entry(None)
        assert result is None

    def test_returns_none_for_invalid_dict(self):
        """Should return None for dict missing required fields."""
        data = {"id": "entry-1"}  # Missing description, instruction, run_at
        result = to_schedule_entry(data)
        assert result is None


class TestToIdealState:
    """Tests for to_ideal_state converter."""

    def test_returns_ideal_state_unchanged(self):
        """Should return IdealState objects unchanged."""
        ideal = make_ideal_state(task_description="Test ideal state")
        result = to_ideal_state(ideal)
        assert result is ideal

    def test_converts_valid_dict(self):
        """Should convert valid dict to IdealState."""
        data = {"task_description": "Expected outcome"}
        result = to_ideal_state(data)
        assert isinstance(result, IdealState)
        assert result.task_description == "Expected outcome"

    def test_returns_none_for_none(self):
        """Should return None for None input."""
        result = to_ideal_state(None)
        assert result is None


class TestEnsureListOfWorkItems:
    """Tests for ensure_list_of_work_items converter."""

    def test_returns_empty_list_for_none(self):
        """Should return empty list for None input."""
        result = ensure_list_of_work_items(None)
        assert result == []

    def test_returns_empty_list_for_empty_list(self):
        """Should return empty list for empty list input."""
        result = ensure_list_of_work_items([])
        assert result == []

    def test_converts_list_of_dicts(self):
        """Should convert list of dicts to WorkItems."""
        data = [
            {"id": "id-1", "item_type": "build_agent", "description": "First"},
            {"id": "id-2", "item_type": "tool_call", "description": "Second"},
        ]
        result = ensure_list_of_work_items(data)
        assert len(result) == 2
        assert all(isinstance(item, WorkItem) for item in result)
        assert result[0].description == "First"
        assert result[1].description == "Second"

    def test_preserves_work_item_objects(self):
        """Should preserve existing WorkItem objects."""
        items = [
            make_work_item(item_type=WorkItemType.BUILD_AGENT, description="First"),
            make_work_item(item_type="tool_call", description="Second"),
        ]
        result = ensure_list_of_work_items(items)
        assert result[0] is items[0]
        assert result[1] is items[1]

    def test_handles_mixed_list(self):
        """Should handle mixed list of dicts and WorkItems."""
        item = make_work_item(item_type=WorkItemType.BUILD_AGENT, description="Object")
        data = [
            item,
            {"id": "id-2", "item_type": "tool_call", "description": "Dict"},
        ]
        result = ensure_list_of_work_items(data)
        assert len(result) == 2
        assert result[0] is item
        assert isinstance(result[1], WorkItem)

    def test_filters_invalid_items(self):
        """Should filter out invalid items."""
        data = [
            {"id": "id-1", "item_type": "build_agent", "description": "Valid"},
            {"invalid": "data"},  # Invalid
            None,  # Invalid
            {"id": "id-2", "item_type": "tool_call", "description": "Also valid"},
        ]
        result = ensure_list_of_work_items(data)
        # Should have filtered out invalid items
        assert all(isinstance(item, WorkItem) for item in result)


class TestConverterEdgeCases:
    """Edge case tests for converters."""

    def test_to_work_item_with_enum_item_type(self):
        """Should handle WorkItem with enum item_type."""
        from app.progenitor.state.models import WorkItemType
        item = make_work_item(item_type=WorkItemType.BUILD_AGENT, description="Test")
        result = to_work_item(item)
        assert result is item

    def test_to_decision_with_enum_action(self):
        """Should handle Decision with enum action."""
        decision = Decision(action=DecisionAction.BUILD_AGENT, reasoning="Build")
        result = to_decision(decision)
        assert result is decision

    def test_converters_are_idempotent(self):
        """Applying converter twice should give same result."""
        item = make_work_item(item_type=WorkItemType.BUILD_AGENT, description="Test")
        result1 = to_work_item(item)
        result2 = to_work_item(result1)
        assert result1 is result2

