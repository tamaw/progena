"""
Time utilities for Progenitor.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
import pytz

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    """Get current UTC time as timezone-aware datetime.

    This replaces the deprecated datetime.utcnow() with
    the recommended timezone-aware approach.
    """
    return datetime.now(timezone.utc)


def utcnow_isoformat() -> str:
    """Get current UTC time as ISO format string."""
    return utcnow().isoformat()


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """Format a datetime for file naming. Uses current time if not provided."""
    if dt is None:
        dt = utcnow()
    return dt.strftime("%Y%m%d_%H%M%S")


class TimeManager:
    """Manages time operations with timezone awareness."""

    def __init__(self, timezone_name: str = "UTC"):
        self.timezone_name = timezone_name
        try:
            self.tz = pytz.timezone(timezone_name)
        except pytz.exceptions.UnknownTimeZoneError as e:
            logger.warning("Unknown timezone '%s', falling back to UTC: %s", timezone_name, e)
            self.tz = pytz.UTC

    def now(self) -> datetime:
        """Get current time in configured timezone."""
        return datetime.now(self.tz)

    def utc_now(self) -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)

    def to_local(self, dt: datetime) -> datetime:
        """Convert datetime to configured timezone."""
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(self.tz)

    def to_utc(self, dt: datetime) -> datetime:
        """Convert datetime to UTC."""
        if dt.tzinfo is None:
            dt = self.tz.localize(dt)
        return dt.astimezone(pytz.UTC)

    def format_time(self, dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
        """Format datetime for display."""
        return self.to_local(dt).strftime(fmt)

    def parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse a time string."""
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                return self.tz.localize(dt)
            except ValueError:
                continue

        return None
