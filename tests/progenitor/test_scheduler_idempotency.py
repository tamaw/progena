"""Focused scheduler idempotency and invalid-entry tests."""

from __future__ import annotations

from datetime import UTC, datetime

from app.progenitor.runtime.scheduler import SCHEDULER_ERROR_METADATA_KEY, Scheduler


def test_restart_within_same_second_does_not_double_fire():
    scheduler = Scheduler("UTC")
    now = datetime(2026, 1, 15, 10, 0, 0, 500000, tzinfo=UTC)
    schedule = [
        {
            "id": "deduped",
            "message": "run once",
            "cron_expression": "0 10 * * *",
            "created_at": "2026-01-14T10:00:00+00:00",
            "last_fired_at": "2026-01-15T10:00:00+00:00",
        }
    ]

    assert scheduler.get_due_items(schedule, now) == []


def test_bad_cron_entries_are_paused_instead_of_hot_fired():
    scheduler = Scheduler("UTC")
    now = datetime(2026, 1, 15, 10, 7, tzinfo=UTC)
    schedule = [
        {
            "id": "bad-cron",
            "message": "broken",
            "cron_expression": "not a cron",
            "created_at": now.isoformat(),
        }
    ]

    assert scheduler.get_due_items(schedule, now) == []
    assert schedule[0]["paused"] is True
    error = schedule[0]["metadata"][SCHEDULER_ERROR_METADATA_KEY]
    assert error["code"] == "invalid_cron_expression"
    assert error["cron_expression"] == "not a cron"


def test_clock_moving_backward_does_not_refire_historical_jobs():
    scheduler = Scheduler("UTC")
    now = datetime(2026, 1, 15, 10, 2, tzinfo=UTC)
    schedule = [
        {
            "id": "historical",
            "message": "already fired",
            "cron_expression": "*/5 * * * *",
            "created_at": "2026-01-15T09:00:00+00:00",
            "last_fired_at": "2026-01-15T10:05:00+00:00",
        }
    ]

    assert scheduler.get_due_items(schedule, now) == []
