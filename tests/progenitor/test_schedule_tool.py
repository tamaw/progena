"""Tests for the schedule management tool (cron + message model)."""

import pytest

from app.progenitor.schedule import (
    ScheduleResult,
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
)
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.tools.schedule import SCHEDULE_TOOL_SPEC
from app.progenitor.utils import utcnow


class TestAddScheduledTask:
    @pytest.mark.asyncio
    async def test_add_task_every_day_at_time(self):
        result, entry_data = await add_scheduled_task(
            user_request="say hello world",
            instruction="Output the message 'Hello, World!' to the user.",
            description="Greeting task",
            cron_expression="0 9 * * *",
        )

        assert result.success is True
        assert result.schedule_id is not None
        assert entry_data is not None
        assert entry_data["message"].startswith("Greeting task")
        assert entry_data["cron_expression"] == "0 9 * * *"
        assert "created_at" in entry_data
        assert "next_run" not in entry_data

    @pytest.mark.asyncio
    async def test_add_task_requires_exactly_one_schedule_field(self):
        result, entry_data = await add_scheduled_task(
            user_request="do something",
            instruction="Some instruction",
        )

        assert result.success is False
        assert "exactly one" in result.message
        assert entry_data is None

    @pytest.mark.asyncio
    async def test_add_task_iso_timestamp(self):
        # Ensure explicit ISO timestamps are stored as 'scheduled_at' (one-off)
        ts = "2026-03-20T09:42:27Z"
        result, entry_data = await add_scheduled_task(
            user_request="remind me",
            instruction="Say hello",
            scheduled_at=ts,
        )

        assert result.success is True
        assert entry_data is not None
        assert "scheduled_at" in entry_data
        assert (
            entry_data.get("scheduled_at") == ts.replace("Z", "+00:00")
            or entry_data.get("scheduled_at") == ts
        )
        assert "cron_expression" not in entry_data
        assert result.next_run is not None

    @pytest.mark.asyncio
    async def test_add_task_iso_malformed_returns_error(self):
        # Malformed ISO should return a clear parse failure (not a silent fallback)
        bad_ts = "2026-03-20T99:99:99Z"
        result, entry_data = await add_scheduled_task(
            user_request="remind me",
            instruction="Say hello",
            scheduled_at=bad_ts,
        )

        assert result.success is False
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
        assert "instruction" in required
        assert "cron_expression" in SCHEDULE_TOOL_SPEC["parameters"].get("properties", {})
        assert "scheduled_at" in SCHEDULE_TOOL_SPEC["parameters"].get("properties", {})


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
