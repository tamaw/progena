"""Tests for the schedule management tool (cron + message model)."""

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
    @pytest.mark.asyncio
    async def test_add_task_every_day_at_time(self):
        result, entry_data = await add_scheduled_task(
            user_request="say hello world",
            time_expression="every day at 9am",
            instruction="Output the message 'Hello, World!' to the user.",
            description="Greeting task",
        )

        assert result.success is True
        assert result.schedule_id is not None
        assert entry_data is not None
        assert entry_data["message"].startswith("Greeting task")
        assert entry_data["cron_expression"] == "0 9 * * *"
        assert "created_at" in entry_data
        assert "next_run" not in entry_data

    @pytest.mark.asyncio
    async def test_add_task_invalid_time(self):
        result, entry_data = await add_scheduled_task(
            user_request="do something",
            time_expression="invalid time format xyz",
            instruction="Some instruction",
        )

        assert result.success is False
        assert "Could not parse" in result.message
        assert entry_data is None


class TestRemoveScheduledTask:
    @pytest.mark.asyncio
    async def test_remove_task_returns_id(self):
        result = await remove_scheduled_task("sched_abc123")
        assert result.success is True
        assert result.schedule_id == "sched_abc123"


class TestListScheduledTasks:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self):
        result = await list_scheduled_tasks([])
        assert result == []

    @pytest.mark.asyncio
    async def test_list_tasks_with_entries(self):
        entries = [
            {
                "id": "sched_1",
                "message": "Morning greeting",
                "cron_expression": "0 9 * * *",
                "created_at": (utcnow()).isoformat(),
            },
        ]

        result = await list_scheduled_tasks(entries)
        assert len(result) == 1
        assert result[0]["message"] == "Morning greeting"
        assert result[0]["cron_expression"] == "0 9 * * *"
        assert "next_run" in result[0]


class TestScheduleToolSpec:
    def test_tool_spec_shape(self):
        assert SCHEDULE_TOOL_SPEC["name"] == "schedule_task"
        required = SCHEDULE_TOOL_SPEC["parameters"].get("required", [])
        assert "user_request" in required
        assert "time_expression" in required
        assert "instruction" in required


class TestScheduleResult:
    def test_success_result(self):
        result = ScheduleResult(
            success=True,
            schedule_id="sched_123",
            message="Task scheduled",
            next_run="2025-01-29T09:00:00Z",
        )
        assert result.success is True
        assert result.schedule_id == "sched_123"


class TestScheduleEntryIntegration:
    def test_schedule_entry_model_dump(self):
        entry = ScheduleEntry(
            id="test_789",
            message="Run this message",
            cron_expression="*/30 * * * *",
            created_at=utcnow(),
        )
        data = entry.model_dump()
        assert data["message"] == "Run this message"
        assert "cron_expression" in data
        assert "created_at" in data
        assert "next_run" not in data
        assert "instruction" not in data

