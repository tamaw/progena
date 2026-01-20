"""Tests for state helpers."""

import pytest
from app.progenitor.state.helpers import (
    normalize_work_item,
    normalize_decision,
    normalize_schedule_entry,
    to_dict,
    is_actionable,
    get_status_value,
    get_item_type_value,
)
from app.progenitor.state.models import (
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    Decision,
    DecisionAction,
    ScheduleEntry,
)


class TestNormalizeWorkItem:
    """Tests for normalize_work_item helper."""

    def test_normalize_none(self):
        """Test normalizing None returns None."""
        assert normalize_work_item(None) is None

    def test_normalize_work_item_passthrough(self):
        """Test that WorkItem passes through unchanged."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test item",
        )
        result = normalize_work_item(item)
        assert result is item

    def test_normalize_dict_basic(self):
        """Test normalizing a basic dict."""
        data = {
            "id": "test-2",
            "item_type": "a2a_call",
            "description": "From dict",
            "status": "pending",
        }
        result = normalize_work_item(data)
        assert isinstance(result, WorkItem)
        assert result.id == "test-2"
        assert result.item_type_value == "a2a_call"
        assert result.description == "From dict"

    def test_normalize_dict_with_invalid_type(self):
        """Test normalizing dict with invalid item_type falls back."""
        data = {
            "id": "test-3",
            "item_type": "invalid_type",
            "description": "Invalid type",
        }
        result = normalize_work_item(data)
        assert result.item_type_value == "direct_answer"

    def test_normalize_dict_with_context(self):
        """Test normalizing dict with context."""
        data = {
            "id": "test-4",
            "item_type": "mcp_call",
            "description": "With context",
            "context": {"method": "execute", "params": {"x": 1}},
            "target_tool": "my_tool",
        }
        result = normalize_work_item(data)
        assert result.context == {"method": "execute", "params": {"x": 1}}
        assert result.target_tool == "my_tool"


class TestNormalizeDecision:
    """Tests for normalize_decision helper."""

    def test_normalize_none(self):
        """Test normalizing None returns None."""
        assert normalize_decision(None) is None

    def test_normalize_decision_passthrough(self):
        """Test that Decision passes through unchanged."""
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Test",
        )
        result = normalize_decision(decision)
        assert result is decision

    def test_normalize_dict(self):
        """Test normalizing a dict to Decision."""
        data = {
            "action": "delegate_a2a",
            "reasoning": "Use existing agent",
            "requires_planning": False,
            "target_capabilities": ["capability_1"],
        }
        result = normalize_decision(data)
        assert isinstance(result, Decision)
        assert result.action == DecisionAction.DELEGATE_A2A
        assert result.reasoning == "Use existing agent"


class TestNormalizeScheduleEntry:
    """Tests for normalize_schedule_entry helper."""

    def test_normalize_none(self):
        """Test normalizing None returns None."""
        assert normalize_schedule_entry(None) is None

    def test_normalize_dict(self):
        """Test normalizing a dict to ScheduleEntry."""
        data = {
            "id": "sched-1",
            "description": "Daily task",
            "interval_minutes": 1440,
            "action_type": "a2a_call",
            "action_config": {"agent": "daily_agent"},
        }
        result = normalize_schedule_entry(data)
        assert isinstance(result, ScheduleEntry)
        assert result.id == "sched-1"
        assert result.interval_minutes == 1440


class TestToDict:
    """Tests for to_dict helper."""

    def test_to_dict_none(self):
        """Test to_dict with None returns empty dict."""
        assert to_dict(None) == {}

    def test_to_dict_from_model(self):
        """Test to_dict from Pydantic model."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
        )
        result = to_dict(item)
        assert isinstance(result, dict)
        assert result["id"] == "test-1"

    def test_to_dict_from_dict(self):
        """Test to_dict from dict passes through."""
        data = {"key": "value"}
        result = to_dict(data)
        assert result == data


class TestIsActionable:
    """Tests for is_actionable helper."""

    def test_actionable_work_item_pending(self):
        """Test WorkItem with pending status is actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
            status=WorkItemStatus.PENDING,
        )
        assert is_actionable(item) is True

    def test_actionable_work_item_in_progress(self):
        """Test WorkItem with in_progress status is actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
            status=WorkItemStatus.IN_PROGRESS,
        )
        assert is_actionable(item) is True

    def test_not_actionable_completed(self):
        """Test WorkItem with completed status is not actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
            status=WorkItemStatus.COMPLETED,
        )
        assert is_actionable(item) is False

    def test_actionable_dict_pending(self):
        """Test dict with pending status is actionable."""
        data = {"status": "pending"}
        assert is_actionable(data) is True

    def test_not_actionable_dict_failed(self):
        """Test dict with failed status is not actionable."""
        data = {"status": "failed"}
        assert is_actionable(data) is False


class TestGetStatusValue:
    """Tests for get_status_value helper."""

    def test_status_from_work_item(self):
        """Test getting status from WorkItem."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
            status=WorkItemStatus.IN_PROGRESS,
        )
        assert get_status_value(item) == "in_progress"

    def test_status_from_dict(self):
        """Test getting status from dict."""
        data = {"status": "completed"}
        assert get_status_value(data) == "completed"


class TestGetItemTypeValue:
    """Tests for get_item_type_value helper."""

    def test_type_from_work_item(self):
        """Test getting item_type from WorkItem."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
        )
        assert get_item_type_value(item) == "build_agent"

    def test_type_from_dict(self):
        """Test getting item_type from dict."""
        data = {"item_type": "mcp_call"}
        assert get_item_type_value(data) == "mcp_call"
