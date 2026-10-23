"""Core schedule evaluation logic."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.progena.cron import default_timezone_name, next_cron_time, resolve_timezone
from src.progena.utils import utcnow

logger = logging.getLogger(__name__)


def parse_schedule_datetime(value: Any) -> datetime | None:
    """Parse a datetime from supported schedule entry formats."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


class Scheduler:
    """Evaluate schedule entries and compute due work."""

    def get_due_items(
        self,
        schedule: list[dict[str, Any]],
        current_time: datetime,
    ) -> list[dict[str, Any]]:
        """Get all schedule items that are due based on cron + last_run/created_at."""
        due_with_time: list[tuple[datetime, dict[str, Any]]] = []

        for item in schedule:
            if item.get("paused"):
                continue
            next_run = self.next_run_for_entry(item)
            if next_run is None:
                continue
            if next_run <= current_time:
                due_with_time.append((next_run, item))

        due_with_time.sort(key=lambda pair: pair[0])
        return [item for _, item in due_with_time]

    def next_run_for_entry(self, item: dict[str, Any]) -> datetime | None:
        """Compute the next run for an entry."""
        scheduled_at = item.get("scheduled_at")
        last_run = parse_schedule_datetime(item.get("last_run"))
        if scheduled_at:
            scheduled_dt = parse_schedule_datetime(scheduled_at)
            if scheduled_dt is None:
                return None
            if last_run is None:
                return scheduled_dt
            return None

        cron_expression = item.get("cron_expression")
        if not isinstance(cron_expression, str):
            return None
        created_at = parse_schedule_datetime(item.get("created_at"))
        reference = last_run or created_at or utcnow()
        # Evaluate cron fields against wall-clock time in the entry's timezone.
        # Fall back to the configured default zone when the entry does not
        # specify one.
        zone = resolve_timezone(item.get("timezone") or default_timezone_name())
        try:
            return next_cron_time(cron_expression, reference, zone)
        except Exception:
            return None

    def is_one_shot_expression(self, cron_expression: str) -> bool:
        """Infer one-shot intent from cron shape."""
        if not cron_expression:
            return False
        parts = cron_expression.split()
        if len(parts) != 5:
            return False
        day = parts[2]
        month = parts[3]
        return day != "*" and month != "*"
