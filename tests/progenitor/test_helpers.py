"""Tests for state helpers."""

from app.progenitor.state.helpers import (
    find_work_item_by_id,
    get_actionable_items,
    get_item_type_value,
    get_status_value,
    is_actionable,
    is_work_item_actionable,
    mark_actionable_items_completed,
    mark_work_item_completed,
    normalize_decision,
    normalize_schedule_entry,
    normalize_work_item,
    to_dict,
)
from app.progenitor.state.models import (
    Decision,
    DecisionAction,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
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
        assert result is not None
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
        assert result is not None
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
        assert result is not None
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
        """Test normalizing a dict to ScheduleEntry (simplified model)."""
        data = {
            "id": "sched-1",
            "description": "Daily task",
            "instruction": "Do something daily",
            "run_at": "2026-01-20T09:00:00+00:00",
            "repeat_minutes": 1440,
        }
        result = normalize_schedule_entry(data)
        assert isinstance(result, ScheduleEntry)
        assert result.id == "sched-1"
        assert result.repeat_minutes == 1440

    def test_normalize_dict_legacy_interval(self):
        """Test that legacy interval_minutes field is not supported."""
        data = {
            "id": "sched-2",
            "description": "Legacy task",
            "instruction": "Test",
            "interval_minutes": 60,  # Old field name - no longer supported
        }
        result = normalize_schedule_entry(data)
        # Without run_at, should return None
        assert result is None


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


class TestMarkWorkItemCompleted:
    """Tests for mark_work_item_completed helper."""

    def test_mark_work_item_model_completed(self):
        """Test marking a WorkItem model as completed."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
            status=WorkItemStatus.PENDING,
        )
        result = {"success": True, "output": "build done"}

        mark_work_item_completed(item, result)

        assert item.status == WorkItemStatus.COMPLETED
        assert item.result == result
        assert item.updated_at is not None

    def test_mark_dict_item_completed(self):
        """Test marking a dict item as completed."""
        item = {"id": "test-2", "status": "pending", "description": "Test dict"}
        result = {"success": False, "error": "build failed"}

        mark_work_item_completed(item, result)

        assert item["status"] == "completed"
        assert item["result"] == result
        assert "updated_at" in item

    def test_mark_in_progress_item_completed(self):
        """Test marking an in_progress item as completed."""
        item = WorkItem(
            id="test-3",
            item_type=WorkItemType.A2A_CALL,
            description="Test",
            status=WorkItemStatus.IN_PROGRESS,
        )
        result = {"success": True}

        mark_work_item_completed(item, result)

        assert item.status == WorkItemStatus.COMPLETED


class TestMarkActionableItemsCompleted:
    """Tests for mark_actionable_items_completed helper."""

    def test_mark_all_pending_completed(self):
        """Test marking all pending items as completed."""
        backlog = [
            WorkItem(id="1", item_type=WorkItemType.BUILD_AGENT, description="A", status=WorkItemStatus.PENDING),
            WorkItem(id="2", item_type=WorkItemType.A2A_CALL, description="B", status=WorkItemStatus.IN_PROGRESS),
            WorkItem(id="3", item_type=WorkItemType.MCP_CALL, description="C", status=WorkItemStatus.COMPLETED),
        ]
        result = {"success": True}

        updated = mark_actionable_items_completed(backlog, result)

        # Items 1 and 2 should be completed, item 3 was already completed
        assert updated[0].status == WorkItemStatus.COMPLETED
        assert updated[1].status == WorkItemStatus.COMPLETED
        assert updated[2].status == WorkItemStatus.COMPLETED
        # Items 1 and 2 should have result attached
        assert updated[0].result == result
        assert updated[1].result == result
        # Item 3 should not have result (was already completed)
        assert updated[2].result is None

    def test_mark_mixed_backlog_completed(self):
        """Test marking a mixed backlog (models and dicts) as completed."""
        backlog = [
            WorkItem(id="1", item_type=WorkItemType.BUILD_AGENT, description="A", status=WorkItemStatus.PENDING),
            {"id": "2", "status": "pending", "description": "B"},
            {"id": "3", "status": "completed", "description": "C"},
        ]
        result = {"success": True}

        updated = mark_actionable_items_completed(backlog, result)

        assert updated[0].status == WorkItemStatus.COMPLETED
        assert updated[1]["status"] == "completed"
        assert updated[2]["status"] == "completed"
        assert updated[0].result == result
        assert updated[1]["result"] == result
        assert updated[2].get("result") is None  # Was already completed

    def test_empty_backlog(self):
        """Test marking an empty backlog."""
        updated = mark_actionable_items_completed([], {"success": True})
        assert updated == []

    def test_no_actionable_items(self):
        """Test backlog with no actionable items."""
        backlog = [
            WorkItem(id="1", item_type=WorkItemType.BUILD_AGENT, description="A", status=WorkItemStatus.COMPLETED),
            {"id": "2", "status": "failed", "description": "B"},
        ]
        result = {"success": True}

        updated = mark_actionable_items_completed(backlog, result)

        # Nothing should change
        assert updated[0].result is None
        assert updated[1].get("result") is None


class TestFindWorkItemById:
    """Tests for find_work_item_by_id helper."""

    def test_find_work_item_model(self):
        """Test finding a WorkItem model by ID."""
        item1 = WorkItem(id="find-1", item_type=WorkItemType.BUILD_AGENT, description="A")
        item2 = WorkItem(id="find-2", item_type=WorkItemType.A2A_CALL, description="B")
        backlog = [item1, item2]

        found = find_work_item_by_id(backlog, "find-2")

        assert found is item2

    def test_find_dict_item(self):
        """Test finding a dict item by ID."""
        backlog = [
            {"id": "dict-1", "description": "A"},
            {"id": "dict-2", "description": "B"},
        ]

        found = find_work_item_by_id(backlog, "dict-1")

        assert found == {"id": "dict-1", "description": "A"}

    def test_find_mixed_backlog(self):
        """Test finding in a mixed backlog."""
        item = WorkItem(id="mix-1", item_type=WorkItemType.BUILD_AGENT, description="A")
        backlog = [
            item,
            {"id": "mix-2", "description": "B"},
        ]

        found = find_work_item_by_id(backlog, "mix-2")
        assert found == {"id": "mix-2", "description": "B"}

        found = find_work_item_by_id(backlog, "mix-1")
        assert found is item

    def test_not_found(self):
        """Test that None is returned when ID not found."""
        backlog = [
            WorkItem(id="a", item_type=WorkItemType.BUILD_AGENT, description="A"),
            {"id": "b", "description": "B"},
        ]

        found = find_work_item_by_id(backlog, "nonexistent")

        assert found is None

    def test_empty_backlog(self):
        """Test finding in an empty backlog."""
        found = find_work_item_by_id([], "any-id")
        assert found is None


class TestIsWorkItemActionable:
    """Tests for is_work_item_actionable helper (canonical implementation)."""

    def test_pending_work_item_is_actionable(self):
        """Test that pending WorkItem is actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
            status=WorkItemStatus.PENDING,
        )
        assert is_work_item_actionable(item) is True

    def test_in_progress_work_item_is_actionable(self):
        """Test that in_progress WorkItem is actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
            status=WorkItemStatus.IN_PROGRESS,
        )
        assert is_work_item_actionable(item) is True

    def test_completed_work_item_not_actionable(self):
        """Test that completed WorkItem is not actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
            status=WorkItemStatus.COMPLETED,
        )
        assert is_work_item_actionable(item) is False

    def test_failed_work_item_not_actionable(self):
        """Test that failed WorkItem is not actionable."""
        item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test",
            status=WorkItemStatus.FAILED,
        )
        assert is_work_item_actionable(item) is False

    def test_none_not_actionable(self):
        """Test that None is not actionable."""
        assert is_work_item_actionable(None) is False


class TestGetActionableItems:
    """Tests for get_actionable_items helper."""

    def test_filters_pending_items(self):
        """Test that only pending/in_progress items are returned."""
        backlog = [
            WorkItem(id="1", item_type=WorkItemType.BUILD_AGENT, description="A", status=WorkItemStatus.PENDING),
            WorkItem(id="2", item_type=WorkItemType.A2A_CALL, description="B", status=WorkItemStatus.IN_PROGRESS),
            WorkItem(id="3", item_type=WorkItemType.MCP_CALL, description="C", status=WorkItemStatus.COMPLETED),
            {"id": "4", "status": "pending", "description": "D"},
            {"id": "5", "status": "failed", "description": "E"},
        ]

        actionable = get_actionable_items(backlog)

        assert len(actionable) == 3
        assert actionable[0].id == "1"
        assert actionable[1].id == "2"
        assert actionable[2]["id"] == "4"

    def test_empty_backlog(self):
        """Test empty backlog returns empty list."""
        assert get_actionable_items([]) == []

    def test_no_actionable_items(self):
        """Test backlog with no actionable items."""
        backlog = [
            WorkItem(id="1", item_type=WorkItemType.BUILD_AGENT, description="A", status=WorkItemStatus.COMPLETED),
            {"id": "2", "status": "failed", "description": "B"},
        ]

        actionable = get_actionable_items(backlog)

        assert actionable == []
