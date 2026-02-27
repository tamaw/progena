"""Tests for scheduler."""

from datetime import timedelta

from app.progenitor.runtime.scheduler import Scheduler
from app.progenitor.runtime.time_utils import utcnow


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

    def test_parse_every_day_at(self):
        """Test parsing 'every day at X' expressions."""
        result = self.scheduler.parse_time_expression("every day at 9am")

        assert result["cron_expression"] == "0 9 * * *"
        assert "description" in result
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is False

    def test_parse_every_minutes(self):
        """Test parsing 'every X minutes' expressions."""
        result = self.scheduler.parse_time_expression("every 30 minutes")

        assert result["cron_expression"] == "*/30 * * * *"
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is False

    def test_parse_every_hours(self):
        """Test parsing 'every X hours' expressions."""
        result = self.scheduler.parse_time_expression("every 2 hours")

        assert result["cron_expression"] == "0 */2 * * *"
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is False

    def test_parse_tomorrow(self):
        """Test parsing 'tomorrow' expression."""
        result = self.scheduler.parse_time_expression("tomorrow")

        assert "cron_expression" in result
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is True

    def test_parse_in_minutes(self):
        """Test parsing 'in X minutes' expression."""
        result = self.scheduler.parse_time_expression("in 15 minutes")

        assert "cron_expression" in result
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is True

    def test_mark_executed(self):
        """Test marking an item executed updates last_run."""
        now = utcnow().replace(second=0, microsecond=0)
        schedule = [
            {
                "id": "task-1",
                "cron_expression": "*/30 * * * *",
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "last_run": None,
            },
            {
                "id": "task-2",
                "cron_expression": "0 * * * *",
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "last_run": None,
            },
        ]

        updated = self.scheduler.mark_executed(schedule, "task-1", now)
        assert updated[0]["last_run"] == now.isoformat()
        assert updated[1]["last_run"] is None

    def test_next_run_for_entry_invalid_cron(self):
        """Invalid cron expressions return None."""
        assert self.scheduler.next_run_for_entry({"cron_expression": "not a cron"}) is None

