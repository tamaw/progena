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
        past_time = (now - timedelta(hours=1)).isoformat()
        future_time = (now + timedelta(hours=1)).isoformat()

        schedule = [
            {"id": "past", "run_at": past_time, "enabled": True},
            {"id": "future", "run_at": future_time, "enabled": True},
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 1
        assert due[0]["id"] == "past"

    def test_get_due_items_interval(self):
        """Test finding due interval-based items."""
        now = utcnow()
        (now - timedelta(minutes=30)).isoformat()
        # For recurring tasks, run_at advances after each run
        # A task is due when run_at <= now
        past_run_at = (now - timedelta(minutes=5)).isoformat()
        future_run_at = (now + timedelta(minutes=30)).isoformat()

        schedule = [
            {
                "id": "interval-due",
                "repeat_minutes": 15,
                "run_at": past_run_at,
                "enabled": True,
            },
            {
                "id": "interval-not-due",
                "repeat_minutes": 60,
                "run_at": future_run_at,
                "enabled": True,
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 1
        assert due[0]["id"] == "interval-due"

    def test_disabled_items_not_due(self):
        """Test that disabled items are not returned."""
        now = utcnow()
        past_time = (now - timedelta(hours=1)).isoformat()

        schedule = [
            {"id": "disabled", "run_at": past_time, "enabled": False},
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 0

    def test_parse_every_day_at(self):
        """Test parsing 'every day at X' expressions."""
        result = self.scheduler.parse_time_expression("every day at 9am")

        # Now returns repeat_minutes (1440 for daily) instead of cron
        assert "repeat_minutes" in result
        assert result["repeat_minutes"] == 1440
        assert "run_at" in result
        assert "description" in result

    def test_parse_every_minutes(self):
        """Test parsing 'every X minutes' expressions."""
        result = self.scheduler.parse_time_expression("every 30 minutes")

        # Uses repeat_minutes instead of interval_minutes
        assert "repeat_minutes" in result
        assert result["repeat_minutes"] == 30

    def test_parse_every_hours(self):
        """Test parsing 'every X hours' expressions."""
        result = self.scheduler.parse_time_expression("every 2 hours")

        # Uses repeat_minutes instead of interval_minutes
        assert "repeat_minutes" in result
        assert result["repeat_minutes"] == 120

    def test_parse_tomorrow(self):
        """Test parsing 'tomorrow' expression."""
        result = self.scheduler.parse_time_expression("tomorrow")

        assert "run_at" in result
        run_at = datetime.fromisoformat(result["run_at"])
        assert run_at > utcnow()

    def test_parse_in_minutes(self):
        """Test parsing 'in X minutes' expression."""
        result = self.scheduler.parse_time_expression("in 15 minutes")

        assert "run_at" in result
        run_at = datetime.fromisoformat(result["run_at"])
        now = utcnow()
        diff = (run_at - now).total_seconds() / 60
        assert 14 <= diff <= 16

    def test_advance_recurring(self):
        """Test advancing recurring task's run_at time."""
        now = utcnow()
        past_run_at = (now - timedelta(minutes=10)).isoformat()

        schedule = [
            {"id": "task-1", "repeat_minutes": 30, "run_at": past_run_at, "last_run": None},
            {"id": "task-2", "repeat_minutes": 60, "run_at": past_run_at, "last_run": None},
        ]

        run_time = now
        updated = self.scheduler.advance_recurring(schedule, "task-1", run_time)

        # task-1's last_run should be set and run_at should be advanced
        assert updated[0]["last_run"] == run_time.isoformat()
        # run_at should now be in the future
        new_run_at = datetime.fromisoformat(updated[0]["run_at"])
        assert new_run_at > run_time

        # task-2 should be unchanged
        assert updated[1]["last_run"] is None

    def test_advance_recurring_one_off_keeps_last_run(self):
        """Test that one-off tasks get last_run set but no run_at advance."""
        now = utcnow()
        past_run_at = (now - timedelta(minutes=5)).isoformat()

        schedule = [
            {"id": "one-off", "run_at": past_run_at, "enabled": True},
        ]

        run_time = now
        updated = self.scheduler.advance_recurring(schedule, "one-off", run_time)

        # last_run should be set
        assert updated[0]["last_run"] == run_time.isoformat()
        # run_at should NOT be advanced (no repeat_minutes)
        assert updated[0]["run_at"] == past_run_at
