"""Tests for scheduler."""

import pytest
from datetime import datetime, timedelta, timezone

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
        past_run = (now - timedelta(minutes=30)).isoformat()

        schedule = [
            {
                "id": "interval-due",
                "interval_minutes": 15,
                "last_run": past_run,
                "enabled": True,
            },
            {
                "id": "interval-not-due",
                "interval_minutes": 60,
                "last_run": past_run,
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

        assert "cron_expression" in result
        assert result["cron_expression"] == "0 9 * * *"

    def test_parse_every_minutes(self):
        """Test parsing 'every X minutes' expressions."""
        result = self.scheduler.parse_time_expression("every 30 minutes")

        assert "interval_minutes" in result
        assert result["interval_minutes"] == 30

    def test_parse_every_hours(self):
        """Test parsing 'every X hours' expressions."""
        result = self.scheduler.parse_time_expression("every 2 hours")

        assert "interval_minutes" in result
        assert result["interval_minutes"] == 120

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

    def test_update_last_run(self):
        """Test updating last_run time."""
        schedule = [
            {"id": "task-1", "interval_minutes": 30, "last_run": None},
            {"id": "task-2", "interval_minutes": 60, "last_run": None},
        ]

        run_time = utcnow()
        updated = self.scheduler.update_last_run(schedule, "task-1", run_time)

        assert updated[0]["last_run"] == run_time.isoformat()
        assert updated[1]["last_run"] is None

    def test_update_last_run_disables_one_off(self):
        """Test that one-off tasks are disabled after running."""
        run_at = utcnow().isoformat()
        schedule = [
            {"id": "one-off", "run_at": run_at, "enabled": True},
        ]

        run_time = utcnow()
        updated = self.scheduler.update_last_run(schedule, "one-off", run_time)

        assert updated[0]["enabled"] is False
