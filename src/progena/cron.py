"""Cron and timezone evaluation shared by Progena schedules and workflow cron triggers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

logger = logging.getLogger(__name__)


def timezone_name(value: Any, fallback: str = "UTC") -> str:
    """Return a valid IANA timezone name, using a valid fallback or UTC."""
    name = value.strip() if isinstance(value, str) else ""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        fallback = fallback.strip() if isinstance(fallback, str) else ""
        try:
            ZoneInfo(fallback)
        except (ZoneInfoNotFoundError, ValueError):
            return "UTC"
        return fallback
    return name


def resolve_timezone(name: str | None) -> tzinfo:
    """Resolve a timezone name to a tzinfo, falling back to UTC if invalid."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown timezone %r; falling back to UTC", name)
        return UTC


def cron_error(expression: str, timezone: str) -> str | None:
    """Why a 5-field cron expression or IANA timezone is unusable; ``None`` if both are valid."""
    if len(expression.split()) != 5:
        return f"cron expression {expression!r} must have 5 fields"
    if not croniter.is_valid(expression):
        return f"invalid cron expression {expression!r}"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"unknown timezone {timezone!r}"
    return None


def next_cron_time(expression: str, after: datetime, timezone: str | tzinfo) -> datetime:
    """The first due time strictly after ``after``, evaluated in wall-clock ``timezone``."""
    zone = resolve_timezone(timezone) if isinstance(timezone, str | None) else timezone
    reference = after if after.tzinfo is not None else after.replace(tzinfo=UTC)
    return cast(datetime, croniter(expression, reference.astimezone(zone)).get_next(datetime))


def latest_cron_time(expression: str, at_or_before: datetime, timezone: str) -> datetime:
    """The latest due time at or before ``at_or_before``."""
    zone = resolve_timezone(timezone)
    reference = at_or_before.astimezone(zone)
    if croniter.match(expression, reference.replace(second=0, microsecond=0)):
        return reference.replace(second=0, microsecond=0)
    return cast(datetime, croniter(expression, reference).get_prev(datetime))
