"""Tests for scheduler (simplified model)."""

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

    def test_get_due_items_recurring(self):
        """Test finding due recurring items (using repeat_minutes)."""
        now = utcnow()
        past_time = (now - timedelta(minutes=30)).isoformat()

        schedule = [
            {
                "id": "recurring-due",
                "run_at": past_time,  # Was due 30 min ago
                "repeat_minutes": 15,
                "enabled": True,
            },
            {
                "id": "recurring-not-due",
                "run_at": (now + timedelta(minutes=30)).isoformat(),  # Due in 30 min
                "repeat_minutes": 60,
                "enabled": True,
            },
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 1
        assert due[0]["id"] == "recurring-due"

    def test_one_off_with_last_run_not_due(self):
        """Test that one-off tasks with last_run set are not due."""
        now = utcnow()
        past_time = (now - timedelta(hours=1)).isoformat()

        schedule = [
            {"id": "completed", "run_at": past_time, "last_run": past_time, "enabled": True},
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert len(due) == 0

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

        assert "run_at" in result
        assert "repeat_minutes" in result
        assert result["repeat_minutes"] == 1440  # 24 hours

    def test_parse_every_minutes(self):
        """Test parsing 'every X minutes' expressions."""
        result = self.scheduler.parse_time_expression("every 30 minutes")

        assert "repeat_minutes" in result
        assert result["repeat_minutes"] == 30

    def test_parse_every_hours(self):
        """Test parsing 'every X hours' expressions."""
        result = self.scheduler.parse_time_expression("every 2 hours")

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
        """Test advancing recurring tasks."""
        now = utcnow()
        run_at = (now - timedelta(minutes=5)).isoformat()
        schedule = [
            {"id": "task-1", "run_at": run_at, "repeat_minutes": 30, "last_run": None},
        ]

        updated = self.scheduler.advance_recurring(schedule, "task-1", now)

        assert updated[0]["last_run"] == now.isoformat()
        # run_at should be advanced
        new_run_at = datetime.fromisoformat(updated[0]["run_at"])
        assert new_run_at > now

    def test_advance_recurring_one_off(self):
        """Test that one-off tasks just get last_run set."""
        now = utcnow()
        run_at = (now - timedelta(minutes=5)).isoformat()
        schedule = [
            {"id": "one-off", "run_at": run_at, "enabled": True},
        ]

        updated = self.scheduler.advance_recurring(schedule, "one-off", now)

        assert updated[0]["last_run"] == now.isoformat()
        # run_at should NOT be advanced for one-off
        assert updated[0]["run_at"] == run_at
