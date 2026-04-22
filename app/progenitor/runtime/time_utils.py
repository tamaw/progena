"""
Time utilities for Progenitor.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Get current UTC time as timezone-aware datetime.

    This replaces the deprecated datetime.utcnow() with
    the recommended timezone-aware approach.
    """
    return datetime.now(UTC)


def utcnow_isoformat() -> str:
    """Get current UTC time as ISO format string."""
    return utcnow().isoformat()


def format_timestamp(dt: datetime | None = None) -> str:
    """Format a datetime for file naming. Uses current time if not provided."""
    if dt is None:
        dt = utcnow()
    return dt.strftime("%Y%m%d_%H%M%S")
