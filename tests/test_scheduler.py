"""Tests for scheduler."""

from datetime import timedelta

from app.progenitor.schedule import Scheduler
from app.progenitor.utils import utcnow


class TestScheduler:
    """Tests for the Scheduler class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.scheduler = Scheduler()

    def test_get_due_items_uses_last_run(self):
        """Due recurring items are selected by computed next run."""
        now = utcnow().replace(second=0, microsecond=0)
        schedule = [
            {
                "id": "due",
                "cron_expression": "* * * * *",
                "last_run": (now - timedelta(minutes=2)).isoformat(),
                "created_at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "id": "not-due",
                "cron_expression": "* * * * *",
                "last_run": now.isoformat(),
                "created_at": (now - timedelta(hours=1)).isoformat(),
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert [item["id"] for item in due] == ["due"]

    def test_get_due_items_uses_created_at_when_no_last_run(self):
        """If no last_run exists, created_at is used as cron reference."""
        now = utcnow().replace(second=0, microsecond=0)
        schedule = [
            {
                "id": "due-from-created",
                "cron_expression": "*/5 * * * *",
                "created_at": (now - timedelta(minutes=10)).isoformat(),
                "last_run": None,
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)
        assert len(due) == 1
        assert due[0]["id"] == "due-from-created"

    def test_next_run_for_entry_invalid_cron(self):
        """Invalid cron expressions return None."""
        assert self.scheduler.next_run_for_entry({"cron_expression": "not a cron"}) is None
