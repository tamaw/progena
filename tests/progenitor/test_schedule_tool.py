"""Tests for the schedule management tool (cron + message model)."""

from app.progenitor.state.models import ScheduleEntry
from app.progenitor.utils import utcnow


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
