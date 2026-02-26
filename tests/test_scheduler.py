"""Tests for scheduler."""

from datetime import datetime, timedelta

from app.progenitor.runtime.scheduler import Scheduler
from app.progenitor.runtime.time_utils import utcnow


class TestScheduler:
    """Tests for the Scheduler class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.scheduler = Scheduler()

    def test_get_due_items_one_off(self):
        """Test finding due one-off scheduled items."""
        now = utcnow()
        past_time = (now - timedelta(hours=1)).replace(second=0, microsecond=0).isoformat()
        future_time = (now + timedelta(hours=1)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {"id": "past", "cron_expression": "* * * * *", "next_run": past_time, "one_off": True, "enabled": True},
            {"id": "future", "cron_expression": "* * * * *", "next_run": future_time, "one_off": True, "enabled": True},
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 1
        assert due[0]["id"] == "past"

    def test_get_due_items_interval(self):
        """Test finding due interval-based items."""
        now = utcnow()
        # For recurring tasks, next_run advances after each run.
        past_run_at = (now - timedelta(minutes=5)).replace(second=0, microsecond=0).isoformat()
        future_run_at = (now + timedelta(minutes=30)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {
                "id": "interval-due",
                "cron_expression": "*/15 * * * *",
                "next_run": past_run_at,
                "enabled": True,
            },
            {
                "id": "interval-not-due",
                "cron_expression": "0 * * * *",
                "next_run": future_run_at,
                "enabled": True,
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 1
        assert due[0]["id"] == "interval-due"

    def test_disabled_items_not_due(self):
        """Test that disabled items are not returned."""
        now = utcnow()
        past_time = (now - timedelta(hours=1)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {"id": "disabled", "cron_expression": "* * * * *", "next_run": past_time, "one_off": True, "enabled": False},
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 0

    def test_parse_every_day_at(self):
        """Test parsing 'every day at X' expressions."""
        result = self.scheduler.parse_time_expression("every day at 9am")

        assert result["cron_expression"] == "0 9 * * *"
        assert "next_run" in result
        assert result["one_off"] is False
        assert "description" in result

    def test_parse_every_minutes(self):
        """Test parsing 'every X minutes' expressions."""
        result = self.scheduler.parse_time_expression("every 30 minutes")

        assert result["cron_expression"] == "*/30 * * * *"
        assert result["one_off"] is False

    def test_parse_every_hours(self):
        """Test parsing 'every X hours' expressions."""
        result = self.scheduler.parse_time_expression("every 2 hours")

        assert result["cron_expression"] == "0 */2 * * *"
        assert result["one_off"] is False

    def test_parse_tomorrow(self):
        """Test parsing 'tomorrow' expression."""
        result = self.scheduler.parse_time_expression("tomorrow")

        assert "next_run" in result
        run_at = datetime.fromisoformat(result["next_run"])
        assert run_at > utcnow()

    def test_parse_in_minutes(self):
        """Test parsing 'in X minutes' expression."""
        result = self.scheduler.parse_time_expression("in 15 minutes")

        assert "next_run" in result
        run_at = datetime.fromisoformat(result["next_run"])
        now = utcnow()
        diff = (run_at - now).total_seconds() / 60
        assert 14 <= diff <= 16

    def test_advance_recurring(self):
        """Test advancing recurring task's next_run time."""
        now = utcnow()
        past_run_at = (now - timedelta(minutes=10)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {"id": "task-1", "cron_expression": "*/30 * * * *", "next_run": past_run_at, "last_run": None},
            {"id": "task-2", "cron_expression": "0 * * * *", "next_run": past_run_at, "last_run": None},
        ]

        run_time = now
        updated = self.scheduler.advance_recurring(schedule, "task-1", run_time)

        # task-1's last_run should be set and next_run should be advanced
        assert updated[0]["last_run"] == run_time.isoformat()
        # next_run should now be in the future
        new_run_at = datetime.fromisoformat(updated[0]["next_run"])
        assert new_run_at > run_time

        # task-2 should be unchanged
        assert updated[1]["last_run"] is None

    def test_advance_recurring_one_off_keeps_last_run(self):
        """Test that one-off tasks get last_run set but no run_at advance."""
        now = utcnow()
        past_run_at = (now - timedelta(minutes=5)).replace(second=0, microsecond=0).isoformat()

        schedule = [
            {"id": "one-off", "cron_expression": "* * * * *", "next_run": past_run_at, "one_off": True, "enabled": True},
        ]

        run_time = now
        updated = self.scheduler.advance_recurring(schedule, "one-off", run_time)

        # last_run should be set
        assert updated[0]["last_run"] == run_time.isoformat()
        # next_run should NOT be advanced for one-off
        assert updated[0]["next_run"] == past_run_at
