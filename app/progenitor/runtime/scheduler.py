"""
Scheduler for Progenitor.

Handles recurring and one-off scheduled tasks.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import re

from .time_utils import utcnow


class Scheduler:
    """Manages scheduled tasks."""

    def __init__(self, timezone: str = "UTC"):
        self.timezone = timezone

    def get_due_items(
        self,
        schedule: list[dict[str, Any]],
        current_time: datetime,
    ) -> list[dict[str, Any]]:
        """Get all schedule items that are due."""
        due = []

        for item in schedule:
            if not item.get("enabled", True):
                continue

            # One-off task
            if item.get("run_at"):
                run_at = self._parse_datetime(item["run_at"])
                if run_at and run_at <= current_time:
                    due.append(item)

            # Recurring task
            elif item.get("cron_expression"):
                next_run = self._get_next_cron_run(
                    item["cron_expression"],
                    item.get("last_run"),
                )
                if next_run and next_run <= current_time:
                    due.append(item)

            # Simple interval
            elif item.get("interval_minutes"):
                last_run = self._parse_datetime(item.get("last_run"))
                if not last_run:
                    due.append(item)
                else:
                    next_run = last_run + timedelta(minutes=item["interval_minutes"])
                    if next_run <= current_time:
                        due.append(item)

        return due

    def parse_time_expression(self, expression: str) -> dict[str, Any]:
        """Parse a natural language time expression."""
        expression = expression.lower().strip()
        result = {}

        # "every day at 9am"
        match = re.search(r"every day at (\d{1,2})(:\d{2})?\s*(am|pm)?", expression)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)[1:]) if match.group(2) else 0
            period = match.group(3)

            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            result["cron_expression"] = f"{minute} {hour} * * *"
            result["description"] = f"Daily at {hour:02d}:{minute:02d}"
            return result

        # "every X minutes/hours"
        match = re.search(r"every (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            if unit in ("hour", "hr"):
                value *= 60

            result["interval_minutes"] = value
            result["description"] = f"Every {value} minutes"
            return result

        # "next Tuesday", "tomorrow", etc.
        if "tomorrow" in expression:
            run_at = utcnow() + timedelta(days=1)
            run_at = run_at.replace(hour=9, minute=0, second=0, microsecond=0)
            result["run_at"] = run_at.isoformat()
            result["description"] = "Tomorrow at 9:00"
            return result

        # "in X minutes/hours"
        match = re.search(r"in (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            if unit in ("hour", "hr"):
                delta = timedelta(hours=value)
            else:
                delta = timedelta(minutes=value)

            run_at = utcnow() + delta
            result["run_at"] = run_at.isoformat()
            result["description"] = f"In {value} {unit}s"
            return result

        return result

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        """Parse a datetime from various formats."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _get_next_cron_run(
        self,
        cron_expr: str,
        last_run: Any,
    ) -> Optional[datetime]:
        """Get the next run time for a cron expression."""
        # Simplified cron parsing (minute hour day month weekday)
        parts = cron_expr.split()
        if len(parts) != 5:
            return None

        minute, hour, day, month, weekday = parts

        now = utcnow()

        # For simple daily schedules
        if all(p == "*" for p in [day, month, weekday]):
            try:
                target_hour = int(hour) if hour != "*" else now.hour
                target_minute = int(minute) if minute != "*" else 0

                next_run = now.replace(
                    hour=target_hour,
                    minute=target_minute,
                    second=0,
                    microsecond=0
                )

                if next_run <= now:
                    next_run += timedelta(days=1)

                # Check against last_run
                last = self._parse_datetime(last_run)
                if last and next_run <= last:
                    next_run += timedelta(days=1)

                return next_run
            except ValueError:
                return None

        return None

    def update_last_run(
        self,
        schedule: list[dict[str, Any]],
        item_id: str,
        run_time: datetime,
    ) -> list[dict[str, Any]]:
        """Update the last_run time for a schedule item."""
        for item in schedule:
            if item.get("id") == item_id:
                item["last_run"] = run_time.isoformat()

                # Disable one-off tasks after running
                if item.get("run_at") and not item.get("cron_expression"):
                    item["enabled"] = False

                break

        return schedule
