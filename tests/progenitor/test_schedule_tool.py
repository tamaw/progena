"""Tests for the schedule management tool (cron + message model)."""

import pytest

from app.progenitor.schedule import ScheduleResult, remove_scheduled_task
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.utils import utcnow


class TestRemoveScheduledTask:
    @pytest.mark.asyncio
    async def test_remove_task_returns_id(self):
        result = await remove_scheduled_task("sched_abc123")
        assert result.success is True
        assert result.schedule_id == "sched_abc123"


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
