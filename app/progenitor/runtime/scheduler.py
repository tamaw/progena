"""
Scheduler for Progenitor.

Handles recurring and one-off scheduled tasks.

Simplified model:
- One-off: run_at is set, repeat_minutes is None. Runs once, then last_run is set.
- Recurring: run_at + repeat_minutes. After each run, run_at advances by repeat_minutes.
"""

import re
from datetime import datetime, timedelta
from typing import Any

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
        """Get all schedule items that are due.

        An item is due if:
        - enabled is True
        - run_at <= current_time
        - For one-off (no repeat_minutes): last_run must be None
        - For recurring: always check run_at (it advances after each execution)
        """
        due = []

        for item in schedule:
            if not item.get("enabled", True):
                continue

            run_at = self._parse_datetime(item.get("run_at"))
            if not run_at:
                continue

            # Check if run_at has passed
            if run_at > current_time:
                continue

            # One-off task: skip if already executed
            if not item.get("repeat_minutes") and item.get("last_run"):
                continue

            due.append(item)

        return due

    def parse_time_expression(self, expression: str) -> dict[str, Any]:
        """Parse a natural language time expression.

        Returns dict with:
        - run_at: ISO datetime string for when to first execute
        - repeat_minutes: Optional int for recurring interval
        - description: Human-readable description
        """
        expression = expression.lower().strip()
        result = {}

        # "every day at 9am" -> daily = 1440 minutes
        match = re.search(r"every day at (\d{1,2})(:\d{2})?\s*(am|pm)?", expression)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)[1:]) if match.group(2) else 0
            period = match.group(3)

            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            # Set run_at to next occurrence of this time
            now = utcnow()
            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)

            result["run_at"] = run_at.isoformat()
            result["repeat_minutes"] = 1440  # 24 hours
            result["description"] = f"Daily at {hour:02d}:{minute:02d}"
            return result

        # "every X minutes/hours"
        match = re.search(r"every (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            repeat_minutes = value * 60 if unit in ("hour", "hr") else value

            result["run_at"] = utcnow().isoformat()  # Start now
            result["repeat_minutes"] = repeat_minutes
            result["description"] = f"Every {repeat_minutes} minutes"
            return result

        # "tomorrow" / "tomorrow at Xam"
        if "tomorrow" in expression:
            run_at = utcnow() + timedelta(days=1)
            # Check for specific time
            time_match = re.search(r"at (\d{1,2})(:\d{2})?\s*(am|pm)?", expression)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)[1:]) if time_match.group(2) else 0
                period = time_match.group(3)
                if period == "pm" and hour != 12:
                    hour += 12
                elif period == "am" and hour == 12:
                    hour = 0
                run_at = run_at.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                run_at = run_at.replace(hour=9, minute=0, second=0, microsecond=0)
            result["run_at"] = run_at.isoformat()
            result["description"] = f"Tomorrow at {run_at.strftime('%H:%M')}"
            return result

        # "in X minutes/hours"
        match = re.search(r"in (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            delta = timedelta(hours=value) if unit in ("hour", "hr") else timedelta(minutes=value)

            run_at = utcnow() + delta
            result["run_at"] = run_at.isoformat()
            result["description"] = f"In {value} {unit}(s)"
            return result

        # "at Xam/pm" (today or tomorrow if time passed)
        match = re.search(r"at (\d{1,2})(:\d{2})?\s*(am|pm)?", expression)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)[1:]) if match.group(2) else 0
            period = match.group(3)

            if period == "pm" and hour != 12:
                hour += 12
            elif period == "am" and hour == 12:
                hour = 0

            now = utcnow()
            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)

            result["run_at"] = run_at.isoformat()
            result["description"] = f"At {hour:02d}:{minute:02d}"
            return result

        return result

    def _parse_datetime(self, value: Any) -> datetime | None:
        """Parse a datetime from various formats."""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def advance_recurring(
        self,
        schedule: list[dict[str, Any]],
        item_id: str,
        run_time: datetime,
    ) -> list[dict[str, Any]]:
        """Update schedule entry after execution.

        For recurring tasks: advances run_at by repeat_minutes.
        For all tasks: sets last_run.
        """
        for item in schedule:
            if item.get("id") == item_id:
                item["last_run"] = run_time.isoformat()

                # Advance recurring tasks
                repeat = item.get("repeat_minutes")
                if repeat:
                    current_run_at = self._parse_datetime(item.get("run_at"))
                    if current_run_at:
                        next_run = current_run_at + timedelta(minutes=repeat)
                        # If next_run is still in the past, advance to future
                        while next_run <= run_time:
                            next_run += timedelta(minutes=repeat)
                        item["run_at"] = next_run.isoformat()

                break

        return schedule
