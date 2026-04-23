"""Tests for scheduler (cron + message model)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.progenitor.runtime.scheduler import SCHEDULER_ERROR_METADATA_KEY, Scheduler
from app.progenitor.runtime.time_utils import utcnow


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

    def test_parse_every_day_at(self):
        result = self.scheduler.parse_time_expression("every day at 9am")
        assert result["cron_expression"] == "0 9 * * *"

    def test_parse_every_minutes(self):
        result = self.scheduler.parse_time_expression("every 30 minutes")
        assert result["cron_expression"] == "*/30 * * * *"
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is False

    def test_parse_every_hours(self):
        result = self.scheduler.parse_time_expression("every 2 hours")
        assert result["cron_expression"] == "0 */2 * * *"
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is False

    def test_parse_tomorrow(self):
        result = self.scheduler.parse_time_expression("tomorrow")
        assert "cron_expression" in result
        assert self.scheduler.is_one_shot_expression(result["cron_expression"]) is True

    def test_parse_in_minutes(self):
        result = self.scheduler.parse_time_expression("in 15 minutes")
        assert "cron_expression" in result

    def test_get_due_items_uses_created_at_when_no_last_run(self):
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

    def test_mark_executed_sets_last_run(self):
        now = utcnow()
        schedule = [
            {"id": "task-1", "cron_expression": "*/30 * * * *", "created_at": now.isoformat()}
        ]
        updated = self.scheduler.mark_executed(schedule, "task-1", now)
        assert updated[0]["last_run"] == now.isoformat()
        assert updated[0]["last_fired_at"] == now.isoformat()

    def test_is_one_shot_expression(self):
        assert self.scheduler.is_one_shot_expression("30 9 27 2 *") is True
        assert self.scheduler.is_one_shot_expression("0 9 * * *") is False

    def test_invalid_cron_is_paused_with_error_metadata(self):
        now = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
        schedule = [
            {
                "id": "invalid-cron",
                "message": "broken",
                "cron_expression": "not a cron",
                "created_at": now.isoformat(),
            }
        ]

        due = self.scheduler.get_due_items(schedule, now)
        updates = self.scheduler.drain_pending_updates(schedule)

        assert due == []
        assert schedule[0]["paused"] is True
        assert updates == [
            (
                "invalid-cron",
                {
                    "paused": True,
                    "metadata": schedule[0]["metadata"],
                },
            )
        ]
        scheduler_error = schedule[0]["metadata"][SCHEDULER_ERROR_METADATA_KEY]
        assert scheduler_error["code"] == "invalid_cron_expression"
        assert scheduler_error["cron_expression"] == "not a cron"

    def test_next_run_for_entry_uses_configured_timezone(self):
        scheduler = Scheduler("Australia/Melbourne")
        item = {
            "id": "tz-test",
            "message": "Morning greeting",
            "cron_expression": "0 9 * * *",
            "created_at": "2026-01-15T10:07:00+00:00",
            "last_run": None,
        }

        next_run = scheduler.next_run_for_entry(item)

        assert next_run == datetime(2026, 1, 15, 22, 0, tzinfo=UTC)

    def test_parse_time_expression_uses_local_timezone_for_one_off_times(self, monkeypatch):
        scheduler = Scheduler("Australia/Melbourne")
        fixed_now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
        monkeypatch.setattr("app.progenitor.runtime.scheduler.utcnow", lambda: fixed_now)

        result = scheduler.parse_time_expression("at 9am")

        assert result["cron_expression"] == "0 9 17 1 *"
        assert scheduler._timezone == ZoneInfo("Australia/Melbourne")

    def test_invalid_scheduler_timezone_fails_loudly(self):
        with pytest.raises(ValueError, match="Unknown scheduler timezone"):
            Scheduler("Mars/Phobos")

    def test_get_due_items_does_not_refire_same_scheduled_instant_after_dispatch(self):
        now = datetime(2026, 1, 15, 10, 0, 0, 500000, tzinfo=UTC)
        schedule = [
            {
                "id": "deduped",
                "message": "run once",
                "cron_expression": "0 10 * * *",
                "created_at": "2026-01-14T10:00:00+00:00",
                "last_fired_at": "2026-01-15T10:00:00+00:00",
                "last_run": None,
            }
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert due == []

    def test_one_off_scheduled_entry_not_due_after_last_fired_at(self):
        now = datetime(2026, 1, 15, 10, 0, 1, tzinfo=UTC)
        schedule = [
            {
                "id": "one-shot",
                "message": "run once",
                "scheduled_at": "2026-01-15T10:00:00+00:00",
                "created_at": "2026-01-14T10:00:00+00:00",
                "last_fired_at": "2026-01-15T10:00:00+00:00",
                "last_run": None,
            }
        ]

        due = self.scheduler.get_due_items(schedule, now)

        assert due == []

    def test_next_run_for_entry_invalid_cron(self):
        assert self.scheduler.next_run_for_entry({"cron_expression": "not a cron"}) is None
