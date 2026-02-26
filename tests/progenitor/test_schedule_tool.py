"""Tests for the schedule management tool (simplified model)."""

from datetime import timedelta

import pytest

from app.progenitor.runtime.time_utils import utcnow
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.tools.schedule import (
    SCHEDULE_TOOL_SPEC,
    ScheduleResult,
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
)


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
        assert entry_data["description"] == "Greeting task"
        assert entry_data["cron_expression"] == "0 9 * * *"
        assert "next_run" in entry_data
        assert entry_data["one_off"] is False

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
        assert "next_run" in entry_data
        assert entry_data["instruction"] == "Remind the user to stretch and take a break."
        assert entry_data["one_off"] is True

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
        assert entry_data["cron_expression"] == "*/15 * * * *"
        assert entry_data["one_off"] is False

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
    async def test_entry_data_simplified_structure(self):
        """Test that entry_data has simplified structure."""
        result, entry_data = await add_scheduled_task(
            user_request="test task",
            time_expression="in 5 minutes",
            instruction="Test instruction",
        )

        assert result.success is True
        assert entry_data is not None
        # Check simplified fields exist
        assert "id" in entry_data
        assert "description" in entry_data
        assert "instruction" in entry_data
        assert "cron_expression" in entry_data
        assert "next_run" in entry_data
        assert "enabled" in entry_data
        assert "one_off" in entry_data
        # Old fields should not exist
        assert "action_type" not in entry_data
        assert "action_config" not in entry_data
        assert "run_at" not in entry_data
        assert "repeat_minutes" not in entry_data

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
        assert "next_run" in entry_data
        assert entry_data["one_off"] is True


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
                "cron_expression": "0 9 * * *",
                "next_run": (utcnow() + timedelta(hours=1)).isoformat(),
                "enabled": True,
            },
            {
                "id": "sched_2",
                "description": "Stock check",
                "instruction": "Check stocks",
                "cron_expression": "0 9 * * *",
                "next_run": (utcnow() + timedelta(hours=2)).isoformat(),
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
                "cron_expression": "*/5 * * * *",
                "next_run": utcnow().isoformat(),
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
    """Integration tests for ScheduleEntry with simplified model."""

    def test_schedule_entry_with_instruction(self):
        """Test ScheduleEntry model with instruction field."""
        entry = ScheduleEntry(
            id="test_123",
            description="Test task",
            instruction="Execute this instruction",
            cron_expression="*/5 * * * *",
            next_run=utcnow(),
        )

        assert entry.instruction == "Execute this instruction"
        assert entry.next_run is not None

    def test_schedule_entry_recurring(self):
        """Test ScheduleEntry recurring cron fields."""
        entry = ScheduleEntry(
            id="test_456",
            description="Recurring task",
            instruction="Do something",
            cron_expression="0 * * * *",
            next_run=utcnow(),
        )

        assert entry.cron_expression == "0 * * * *"
        assert entry.one_off is False
        assert entry.enabled is True

    def test_schedule_entry_model_dump(self):
        """Test that model_dump includes simplified fields."""
        entry = ScheduleEntry(
            id="test_789",
            description="Dump test",
            instruction="Test instruction",
            cron_expression="*/30 * * * *",
            next_run=utcnow(),
        )

        data = entry.model_dump()
        assert "instruction" in data
        assert "cron_expression" in data
        assert "next_run" in data
        assert data["instruction"] == "Test instruction"
        assert data["cron_expression"] == "*/30 * * * *"
        # Old fields should not be present
        assert "run_at" not in data
        assert "repeat_minutes" not in data
        assert "user_request" not in data
        assert "action_type" not in data
