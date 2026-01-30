"""Tests for the schedule management tool."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import timedelta

from app.progenitor.tools.schedule import (
    add_scheduled_task,
    remove_scheduled_task,
    list_scheduled_tasks,
    ScheduleResult,
    SCHEDULE_TOOL_SPEC,
)
from app.progenitor.state.models import ScheduleEntry, DecisionAction
from app.progenitor.runtime.time_utils import utcnow


class TestAddScheduledTask:
    """Tests for add_scheduled_task function."""

    @pytest.mark.asyncio
    async def test_add_task_every_day_at_time(self):
        """Test adding a recurring task at a specific time like 'every day at 9am'."""
        result, entry_data = await add_scheduled_task(
            user_request="say hello world",
            time_expression="every day at 9am",
            instruction="Output the message 'Hello, World!' to the user.",
            description="Greeting task",
        )

        assert result.success is True
        assert result.schedule_id is not None
        assert result.schedule_id.startswith("sched_")
        assert "Greeting task" in result.message
        assert entry_data is not None
        assert entry_data["instruction"] == "Output the message 'Hello, World!' to the user."
        assert entry_data["user_request"] == "say hello world"
        assert entry_data["description"] == "Greeting task"
        assert entry_data["cron_expression"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_add_task_in_minutes(self):
        """Test adding a task to run 'in 30 minutes'."""
        result, entry_data = await add_scheduled_task(
            user_request="remind me to stretch",
            time_expression="in 30 minutes",
            instruction="Remind the user to stretch and take a break.",
        )

        assert result.success is True
        assert entry_data is not None
        assert "run_at" in entry_data
        assert entry_data["instruction"] == "Remind the user to stretch and take a break."

    @pytest.mark.asyncio
    async def test_add_recurring_task(self):
        """Test adding a recurring task like 'every day at 9am'."""
        result, entry_data = await add_scheduled_task(
            user_request="check stock prices",
            time_expression="every day at 9am",
            instruction="Check the current stock prices and report them to the user.",
            recurring=True,
        )

        assert result.success is True
        assert entry_data is not None
        assert "cron_expression" in entry_data
        assert entry_data["cron_expression"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_add_task_every_n_minutes(self):
        """Test adding a task that runs every N minutes."""
        result, entry_data = await add_scheduled_task(
            user_request="ping server",
            time_expression="every 15 minutes",
            instruction="Check if the server is responsive.",
        )

        assert result.success is True
        assert entry_data is not None
        assert entry_data["interval_minutes"] == 15

    @pytest.mark.asyncio
    async def test_add_task_invalid_time(self):
        """Test that invalid time expressions fail gracefully."""
        result, entry_data = await add_scheduled_task(
            user_request="do something",
            time_expression="invalid time format xyz",
            instruction="Some instruction",
        )

        assert result.success is False
        assert "Could not parse" in result.message
        assert entry_data is None

    @pytest.mark.asyncio
    async def test_add_task_default_description(self):
        """Test that description defaults to user request if not provided."""
        result, entry_data = await add_scheduled_task(
            user_request="send daily summary",
            time_expression="in 10 minutes",
            instruction="Generate and send a daily summary.",
        )

        assert result.success is True
        assert entry_data is not None
        assert "send daily summary" in entry_data["description"]

    @pytest.mark.asyncio
    async def test_entry_data_has_correct_action_type(self):
        """Test that entry_data has correct action_type."""
        result, entry_data = await add_scheduled_task(
            user_request="test task",
            time_expression="in 5 minutes",
            instruction="Test instruction",
        )

        assert result.success is True
        assert entry_data is not None
        assert entry_data["action_type"] == DecisionAction.ANSWER_DIRECTLY.value

    @pytest.mark.asyncio
    async def test_add_task_tomorrow(self):
        """Test adding a task for tomorrow."""
        result, entry_data = await add_scheduled_task(
            user_request="say good morning",
            time_expression="tomorrow",
            instruction="Say good morning to the user.",
        )

        assert result.success is True
        assert entry_data is not None
        assert "run_at" in entry_data


class TestRemoveScheduledTask:
    """Tests for remove_scheduled_task function."""

    @pytest.mark.asyncio
    async def test_remove_task_returns_id(self):
        """Test that remove returns the ID to be removed."""
        result = await remove_scheduled_task("sched_abc123")

        assert result.success is True
        assert result.schedule_id == "sched_abc123"
        assert "removal" in result.message.lower()


class TestListScheduledTasks:
    """Tests for list_scheduled_tasks function."""

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self):
        """Test listing tasks when schedule is empty."""
        result = await list_scheduled_tasks([])

        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_list_tasks_with_entries(self):
        """Test listing tasks with entries."""
        entries = [
            {
                "id": "sched_1",
                "description": "Morning greeting",
                "instruction": "Say good morning",
                "run_at": (utcnow() + timedelta(hours=1)).isoformat(),
                "enabled": True,
            },
            {
                "id": "sched_2",
                "description": "Stock check",
                "instruction": "Check stocks",
                "cron_expression": "0 9 * * *",
                "enabled": True,
            },
        ]

        result = await list_scheduled_tasks(entries)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["description"] == "Morning greeting"
        assert result[1]["description"] == "Stock check"
        assert result[0]["id"] == "sched_1"
        assert result[1]["id"] == "sched_2"

    @pytest.mark.asyncio
    async def test_list_tasks_includes_instruction(self):
        """Test that listed tasks include instruction field."""
        entries = [
            {
                "id": "sched_1",
                "description": "Test task",
                "instruction": "Test instruction here",
                "run_at": utcnow().isoformat(),
            },
        ]

        result = await list_scheduled_tasks(entries)

        assert result[0]["instruction"] == "Test instruction here"


class TestScheduleToolSpec:
    """Tests for the SCHEDULE_TOOL_SPEC definition."""

    def test_tool_spec_has_required_fields(self):
        """Test that tool spec has all required fields."""
        assert "name" in SCHEDULE_TOOL_SPEC
        assert "description" in SCHEDULE_TOOL_SPEC
        assert "parameters" in SCHEDULE_TOOL_SPEC

    def test_tool_spec_name(self):
        """Test tool spec name."""
        assert SCHEDULE_TOOL_SPEC["name"] == "schedule_task"

    def test_tool_spec_parameters(self):
        """Test tool spec parameters are defined."""
        params = SCHEDULE_TOOL_SPEC["parameters"]
        assert "properties" in params
        props = params["properties"]
        assert "user_request" in props
        assert "time_expression" in props
        assert "instruction" in props

    def test_tool_spec_required_params(self):
        """Test tool spec required parameters."""
        params = SCHEDULE_TOOL_SPEC["parameters"]
        required = params.get("required", [])
        assert "user_request" in required
        assert "time_expression" in required
        assert "instruction" in required


class TestScheduleResult:
    """Tests for the ScheduleResult model."""

    def test_success_result(self):
        """Test creating a success result."""
        result = ScheduleResult(
            success=True,
            schedule_id="sched_123",
            message="Task scheduled",
            next_run="2025-01-29T09:00:00Z",
        )

        assert result.success is True
        assert result.schedule_id == "sched_123"
        assert result.message == "Task scheduled"
        assert result.next_run == "2025-01-29T09:00:00Z"

    def test_failure_result(self):
        """Test creating a failure result."""
        result = ScheduleResult(
            success=False,
            message="Could not parse time",
        )

        assert result.success is False
        assert result.schedule_id is None
        assert "Could not parse" in result.message


class TestScheduleEntryIntegration:
    """Integration tests for ScheduleEntry with new fields."""

    def test_schedule_entry_with_instruction(self):
        """Test ScheduleEntry model with instruction field."""
        entry = ScheduleEntry(
            id="test_123",
            description="Test task",
            instruction="Execute this instruction",
            user_request="Original user request",
            run_at=utcnow(),
        )

        assert entry.instruction == "Execute this instruction"
        assert entry.user_request == "Original user request"

    def test_schedule_entry_defaults(self):
        """Test ScheduleEntry default values."""
        entry = ScheduleEntry(
            id="test_456",
            description="Minimal entry",
        )

        assert entry.instruction is None
        assert entry.user_request is None
        assert entry.enabled is True
        assert entry.action_type == DecisionAction.ANSWER_DIRECTLY

    def test_schedule_entry_model_dump(self):
        """Test that model_dump includes new fields."""
        entry = ScheduleEntry(
            id="test_789",
            description="Dump test",
            instruction="Test instruction",
            user_request="Test request",
            run_at=utcnow(),
        )

        data = entry.model_dump()
        assert "instruction" in data
        assert "user_request" in data
        assert data["instruction"] == "Test instruction"
        assert data["user_request"] == "Test request"
