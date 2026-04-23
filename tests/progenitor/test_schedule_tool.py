"""Tests for the schedule management tool (cron + message model)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.runtime.scheduler import SCHEDULER_ERROR_METADATA_KEY
from app.progenitor.runtime.time_utils import utcnow
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.tools.schedule import (
    SCHEDULE_TOOL_SPEC,
    ScheduleResult,
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
    update_scheduled_task,
)


class TestAddScheduledTask:
    @pytest.mark.asyncio
    async def test_add_task_every_day_at_time(self):
        result, entry_data = await add_scheduled_task(
            user_request="say hello world",
            time_expression="every day at 9am",
            instruction="Output the message 'Hello, World!' to the user.",
            description="Greeting task",
            timezone="Australia/Melbourne",
        )

        assert result.success is True
        assert result.schedule_id is not None
        assert entry_data is not None
        assert entry_data["message"].startswith("Greeting task")
        assert entry_data["cron_expression"] == "0 9 * * *"
        assert entry_data["timezone"] == "Australia/Melbourne"
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

    @pytest.mark.asyncio
    async def test_add_task_iso_timestamp(self):
        # Ensure explicit ISO timestamps are stored as 'scheduled_at' (one-off)
        ts = "2026-03-20T09:42:27Z"
        result, entry_data = await add_scheduled_task(
            user_request="remind me",
            time_expression=f"at {ts}",
            instruction="Say hello",
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
            time_expression=f"at {bad_ts}",
            instruction="Say hello",
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
                "created_at": "2026-01-15T10:07:00+00:00",
                "timezone": "Australia/Melbourne",
            },
        ]

        result = await list_scheduled_tasks(entries)
        assert len(result) == 1
        assert result[0]["message"] == "Morning greeting"
        assert result[0]["cron_expression"] == "0 9 * * *"
        assert result[0]["next_run"] == "2026-01-15T22:00:00+00:00"
        assert result[0]["timezone"] == "Australia/Melbourne"

    @pytest.mark.asyncio
    async def test_list_tasks_surfaces_paused_invalid_entry(self):
        entries = [
            {
                "id": "sched_bad",
                "message": "Broken cron",
                "cron_expression": "bad cron",
                "created_at": utcnow().isoformat(),
                "paused": True,
                "metadata": {
                    SCHEDULER_ERROR_METADATA_KEY: {
                        "code": "invalid_cron_expression",
                        "message": "bad cron syntax",
                    }
                },
            }
        ]

        result = await list_scheduled_tasks(entries)

        assert result[0]["status"] == "paused_invalid"
        assert result[0]["next_run"] == "Paused"
        assert result[0]["metadata"][SCHEDULER_ERROR_METADATA_KEY]["message"] == "bad cron syntax"

    @pytest.mark.asyncio
    async def test_update_task_clears_scheduler_error_and_unpauses(self):
        state_manager = MagicMock()
        state_manager.get_schedule = AsyncMock(
            return_value=[
                ScheduleEntry(
                    id="sched_bad",
                    message="Broken cron",
                    cron_expression="bad cron",
                    created_at=utcnow(),
                    paused=True,
                    metadata={
                        SCHEDULER_ERROR_METADATA_KEY: {
                            "code": "invalid_cron_expression",
                            "message": "bad cron syntax",
                        },
                        "source": "scheduler",
                    },
                )
            ]
        )
        state_manager.update_schedule_entry = AsyncMock(return_value=True)

        result = await update_scheduled_task(
            "sched_bad",
            {"cron_expression": "0 9 * * *"},
            state_manager=state_manager,
        )

        assert result.success is True
        state_manager.update_schedule_entry.assert_awaited_once()
        args = state_manager.update_schedule_entry.await_args.args
        assert args[0] == "sched_bad"
        assert args[1]["paused"] is False
        assert args[1]["metadata"] == {"source": "scheduler"}


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
        assert "timezone" in data
        assert "next_run" not in data
        assert "instruction" not in data
