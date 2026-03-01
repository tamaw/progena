"""Scheduler for Progenitor using cron expressions."""

import re
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
        """Get all schedule items that are due based on cron + last_run/created_at."""
        due: list[dict[str, Any]] = []
        due_with_time: list[tuple[datetime, dict[str, Any]]] = []

        for item in schedule:
            next_run = self.next_run_for_entry(item)
            if not next_run:
                continue
            if next_run <= current_time:
                due_with_time.append((next_run, item))

        due_with_time.sort(key=lambda pair: pair[0])
        for _, item in due_with_time:
            due.append(item)
        return due

    def parse_time_expression(self, expression: str) -> dict[str, Any]:
        """Parse a natural language time expression.

        Returns dict with:
        - cron_expression: cron expression used for scheduling
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

    def mark_executed(
        self,
        schedule: list[dict[str, Any]],
        item_id: str,
        run_time: datetime,
    ) -> list[dict[str, Any]]:
        """Update schedule entry after execution."""
        for item in schedule:
            if item.get("id") == item_id:
                item["last_run"] = run_time.isoformat()
                break

        return schedule

    def next_run_for_entry(self, item: dict[str, Any]) -> datetime | None:
        """Compute next run for an entry from cron_expression and reference time."""
        cron_expression = item.get("cron_expression")
        if not isinstance(cron_expression, str):
            return None
        last_run = self._parse_datetime(item.get("last_run"))
        created_at = self._parse_datetime(item.get("created_at"))
        reference = last_run or created_at or utcnow()
        try:
            return croniter(cron_expression, reference).get_next(datetime)
        except Exception:
            return None

    def is_one_shot_expression(self, cron_expression: str) -> bool:
        """Infer one-shot intent from cron shape.

        Expressions with fixed day and month are treated as one-shot.
        """
        parts = cron_expression.split()
        if len(parts) != 5:
            return False
        day = parts[2]
        month = parts[3]
        return day != "*" and month != "*"
