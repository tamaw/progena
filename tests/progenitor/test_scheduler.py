"""Tests for scheduler (cron + message model)."""

from datetime import UTC, datetime, timedelta

from app.progenitor.schedule import Scheduler


class TestScheduler:
    """Tests for the Scheduler class."""

    def setup_method(self):
        self.scheduler = Scheduler()

    def test_get_due_items(self):
        now = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
        created_past = (now - timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()
        created_recent = (now - timedelta(minutes=5)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {
                "id": "due",
                "message": "run now",
                "cron_expression": "*/15 * * * *",
                "created_at": created_past,
            },
            {
                "id": "not-due",
                "message": "later",
                "cron_expression": "0 * * * *",
                "created_at": created_recent,
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)
        assert len(due) == 1
        assert due[0]["id"] == "due"

    def test_is_one_shot_expression(self):
        assert self.scheduler.is_one_shot_expression("30 9 27 2 *") is True
        assert self.scheduler.is_one_shot_expression("0 9 * * *") is False
