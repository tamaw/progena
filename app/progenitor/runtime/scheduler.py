"""Scheduler for Progenitor using cron expressions."""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from .time_utils import utcnow

logger = logging.getLogger(__name__)
SCHEDULER_ERROR_METADATA_KEY = "scheduler_error"
_PENDING_SCHEDULE_UPDATES_KEY = "_scheduler_pending_updates"


class Scheduler:
    """Manages scheduled tasks."""

    def __init__(self, timezone: str = "UTC"):
        self.timezone = str(timezone or "UTC")
        try:
            self._timezone = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown scheduler timezone: {self.timezone}") from exc

    def get_due_items(
        self,
        schedule: list[dict[str, Any]],
        current_time: datetime,
    ) -> list[dict[str, Any]]:
        """Get all schedule items that are due based on cron + last_run/created_at."""
        due: list[dict[str, Any]] = []
        due_with_time: list[tuple[datetime, dict[str, Any]]] = []

        for item in schedule:
            if item.get("paused"):
                continue
            next_run = self.next_run_for_entry(item)
            if not next_run:
                continue
            if next_run < current_time:
                due_with_time.append((next_run, item))

        due_with_time.sort(key=lambda pair: pair[0])
        for _, item in due_with_time:
            due.append(item)
        return due

    def drain_pending_updates(
        self, schedule: list[dict[str, Any]]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return and clear schedule-entry updates discovered during evaluation."""
        updates: list[tuple[str, dict[str, Any]]] = []
        for item in schedule:
            pending = item.pop(_PENDING_SCHEDULE_UPDATES_KEY, None)
            schedule_id = str(item.get("id") or "").strip()
            if not schedule_id or not isinstance(pending, dict):
                continue
            updates.append((schedule_id, pending))
        return updates

    def parse_time_expression(self, expression: str) -> dict[str, Any]:
        """Parse a natural language time expression.

        Returns dict with:
        - cron_expression: cron expression used for scheduling
        - scheduled_at: ISO datetime for one-off events (preferred if provided)
        - description: Human-readable description
        """
        original = expression.strip()
        expression = original.lower()
        result: dict[str, Any] = {}
        now = utcnow().astimezone(self._timezone)

        # If the expression is (or contains) an ISO8601 datetime, prefer that
        # to avoid mis-parsing numeric date parts with the "at" regex.
        iso_try = original
        if iso_try.lower().startswith("at "):
            iso_try = iso_try[3:].strip()
        parsed_dt = self._normalize_datetime(
            self._parse_datetime(iso_try),
            self._timezone,
            assume_local_if_naive=True,
        )
        # If the input looks like an ISO datetime but doesn't parse, treat it as a hard
        # parse failure to avoid falling back to ambiguous natural-language rules.
        if re.search(r"\d{4}-\d{2}-\d{2}T", original) and not parsed_dt:
            return result

        if parsed_dt:
            # Normalize to UTC-aware ISO string
            result["scheduled_at"] = parsed_dt.astimezone(UTC).isoformat()
            result["description"] = (
                f"One-off at {parsed_dt.strftime('%Y-%m-%d %H:%M:%S %Z') or parsed_dt.isoformat()}"
            )
            return result

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
                item["last_fired_at"] = run_time.isoformat()
                break

        return schedule

    def next_run_for_entry(self, item: dict[str, Any]) -> datetime | None:
        """Compute next run for an entry.

        Prefer explicit 'scheduled_at' for one-off events. Fallback to cron_expression
        for recurring or unspecified schedules.
        """
        if item.get("paused"):
            return None

        timezone = self._entry_timezone(item)
        if timezone is None:
            return None

        # One-off explicit timestamp takes precedence
        scheduled_at = item.get("scheduled_at")
        last_run = self._normalize_datetime(self._parse_datetime(item.get("last_run")), timezone)
        last_fired_at = self._normalize_datetime(
            self._parse_datetime(item.get("last_fired_at")),
            timezone,
        )
        if scheduled_at:
            scheduled_dt = self._normalize_datetime(
                self._parse_datetime(scheduled_at),
                timezone,
                assume_local_if_naive=True,
            )
            if not scheduled_dt:
                return None
            # If the task hasn't run yet, return the scheduled time; otherwise no next run
            if not last_run and not last_fired_at:
                return scheduled_dt.astimezone(UTC)
            return None

        # Otherwise use cron_expression
        cron_expression = item.get("cron_expression")
        if not isinstance(cron_expression, str):
            return None
        created_at = self._normalize_datetime(
            self._parse_datetime(item.get("created_at")), timezone
        )
        reference_candidates = [
            candidate
            for candidate in (last_run, last_fired_at, created_at)
            if candidate is not None
        ]
        reference = max(reference_candidates) if reference_candidates else utcnow()
        reference_local = self._normalize_datetime(reference, timezone)
        if reference_local is None:
            return None
        try:
            next_run = cast(datetime, croniter(cron_expression, reference_local).get_next(datetime))
            normalized_next_run = self._normalize_datetime(
                next_run,
                timezone,
                assume_local_if_naive=True,
            )
            if normalized_next_run is None:
                return None
            return normalized_next_run.astimezone(UTC)
        except Exception as exc:
            self._mark_invalid_cron_entry(item, cron_expression, exc)
            return None

    def _entry_timezone(self, item: dict[str, Any]) -> ZoneInfo | None:
        """Resolve the timezone for a schedule entry."""
        timezone_name = str(item.get("timezone") or self.timezone or "UTC")
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            metadata_value = item.get("metadata")
            metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
            metadata[SCHEDULER_ERROR_METADATA_KEY] = {
                "code": "invalid_timezone",
                "message": str(exc),
                "timezone": timezone_name,
                "recorded_at": utcnow().isoformat(),
            }
            item["paused"] = True
            item["metadata"] = metadata
            item[_PENDING_SCHEDULE_UPDATES_KEY] = {"paused": True, "metadata": metadata}
            logger.warning(
                "Paused invalid schedule entry %s with timezone %r: %s",
                item.get("id"),
                timezone_name,
                exc,
            )
            return None

    def _normalize_datetime(
        self,
        dt: datetime | None,
        timezone: ZoneInfo,
        *,
        assume_local_if_naive: bool = False,
    ) -> datetime | None:
        """Normalize datetimes for scheduler calculations."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            if assume_local_if_naive:
                return dt.replace(tzinfo=timezone)
            return dt.replace(tzinfo=UTC).astimezone(timezone)
        return dt.astimezone(timezone)

    def _mark_invalid_cron_entry(
        self,
        item: dict[str, Any],
        cron_expression: str,
        exc: Exception,
    ) -> None:
        """Pause an invalid cron entry so the scheduler stops re-evaluating it."""
        metadata_value = item.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
        metadata[SCHEDULER_ERROR_METADATA_KEY] = {
            "code": "invalid_cron_expression",
            "message": str(exc),
            "cron_expression": cron_expression,
            "recorded_at": utcnow().isoformat(),
        }
        item["paused"] = True
        item["metadata"] = metadata
        item[_PENDING_SCHEDULE_UPDATES_KEY] = {
            "paused": True,
            "metadata": metadata,
        }
        logger.warning(
            "Paused invalid schedule entry %s with cron %r: %s",
            item.get("id"),
            cron_expression,
            exc,
        )

    def is_one_shot_expression(self, cron_expression: str) -> bool:
        """Infer one-shot intent from cron shape or explicit scheduled_at.

        Expressions with fixed day and month are treated as one-shot. If a
        scheduled_at value exists the entry is one-shot.
        """
        if not cron_expression:
            return False
        parts = cron_expression.split()
        if len(parts) != 5:
            return False
        day = parts[2]
        month = parts[3]
        return day != "*" and month != "*"
