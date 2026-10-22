"""Behavioral coverage for schedule selection and one-shot detection."""

from datetime import UTC, datetime, timedelta

import pytest

from app.progenitor.schedule import Scheduler


class TestScheduler:
    """Tests for the Scheduler class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.scheduler = Scheduler()

    def test_get_due_items_uses_last_run(self):
        """Due recurring items are selected by computed next run."""
        now = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
        schedule = [
            {
                "id": "due",
                "cron_expression": "* * * * *",
                "last_run": (now - timedelta(minutes=2)).isoformat(),
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "timezone": "UTC",
            },
            {
                "id": "not-due",
                "cron_expression": "* * * * *",
                "last_run": now.isoformat(),
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "timezone": "UTC",
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert [item["id"] for item in due] == ["due"]

    def test_get_due_items_uses_created_at_when_no_last_run(self):
        """If no last_run exists, created_at is used as cron reference."""
        now = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
        schedule = [
            {
                "id": "due-from-created",
                "cron_expression": "*/5 * * * *",
                "created_at": (now - timedelta(minutes=10)).isoformat(),
                "last_run": None,
                "timezone": "UTC",
            },
            {
                "id": "not-due-from-created",
                "cron_expression": "0 * * * *",
                "created_at": (now - timedelta(minutes=5)).isoformat(),
                "last_run": None,
                "timezone": "UTC",
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)
        assert [item["id"] for item in due] == ["due-from-created"]

    def test_next_run_for_entry_invalid_cron(self):
        """Invalid cron expressions return None."""
        assert self.scheduler.next_run_for_entry({"cron_expression": "not a cron"}) is None

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [("30 9 27 2 *", True), ("0 9 * * *", False)],
    )
    def test_is_one_shot_expression(self, expression, expected):
        assert self.scheduler.is_one_shot_expression(expression) is expected
