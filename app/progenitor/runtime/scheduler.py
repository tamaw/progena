"""Scheduler for Progenitor using cron expressions."""

import re
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from croniter import croniter

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
        - next_run <= current_time
        - For one-off tasks: last_run must be None
        """
        due = []

        for item in schedule:
            if not item.get("enabled", True):
                continue

            next_run = self._parse_datetime(item.get("next_run"))
            if not next_run:
                continue

            if next_run > current_time:
                continue

            if item.get("one_off", False) and item.get("last_run"):
                continue

            due.append(item)

        return due

    def parse_time_expression(self, expression: str) -> dict[str, Any]:
        """Parse a natural language time expression.

        Returns dict with:
        - cron_expression: cron expression used for scheduling
        - next_run: ISO datetime string for next execution
        - one_off: whether the task should run only once
        - description: Human-readable description
        """
        expression = expression.lower().strip()
        result: dict[str, Any] = {}
        now = utcnow()

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

            cron_expression = f"{minute} {hour} * * *"
            result["cron_expression"] = cron_expression
            result["next_run"] = self._next_run_iso(cron_expression, now)
            result["one_off"] = False
            result["description"] = f"Daily at {hour:02d}:{minute:02d}"
            return result

        # "every X minutes/hours"
        match = re.search(r"every (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            if unit in ("hour", "hr"):
                cron_expression = f"0 */{value} * * *"
                result["description"] = f"Every {value} hour(s)"
            else:
                cron_expression = f"*/{value} * * * *"
                result["description"] = f"Every {value} minute(s)"

            result["cron_expression"] = cron_expression
            result["next_run"] = self._next_run_iso(cron_expression, now)
            result["one_off"] = False
            return result

        # "tomorrow" / "tomorrow at Xam"
        if "tomorrow" in expression:
            run_at = now + timedelta(days=1)
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
            cron_expression = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"
            result["cron_expression"] = cron_expression
            result["next_run"] = run_at.isoformat()
            result["one_off"] = True
            result["description"] = f"Tomorrow at {run_at.strftime('%H:%M')}"
            return result

        # "in X minutes/hours"
        match = re.search(r"in (\d+)\s*(minute|hour|min|hr)s?", expression)
        if match:
            value = int(match.group(1))
            unit = match.group(2)

            delta = timedelta(hours=value) if unit in ("hour", "hr") else timedelta(minutes=value)

            run_at = now + delta
            cron_expression = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"
            result["cron_expression"] = cron_expression
            result["next_run"] = run_at.isoformat()
            result["one_off"] = True
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

            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)

            cron_expression = f"{run_at.minute} {run_at.hour} {run_at.day} {run_at.month} *"
            result["cron_expression"] = cron_expression
            result["next_run"] = run_at.isoformat()
            result["one_off"] = True
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

        For recurring tasks: advances next_run using croniter.
        For all tasks: sets last_run.
        """
        for item in schedule:
            if item.get("id") == item_id:
                item["last_run"] = run_time.isoformat()

                if not item.get("one_off", False):
                    cron_expression = item.get("cron_expression")
                    if isinstance(cron_expression, str):
                        with suppress(ValueError, KeyError):
                            item["next_run"] = croniter(cron_expression, run_time).get_next(datetime).isoformat()

                break

        return schedule

    def _next_run_iso(self, cron_expression: str, base_time: datetime) -> str:
        """Get the next run time for a cron expression."""
        return croniter(cron_expression, base_time).get_next(datetime).isoformat()
