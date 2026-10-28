"""
Utility functions for Progena.

Helpers for working with data structures and environment variable parsing.
"""

import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def env_bool(key: str, default: bool = False) -> bool:
    """Parse boolean environment variable.

    Accepts: true, 1, yes, on (case-insensitive) for True
    Accepts: false, 0, no, off (case-insensitive) for False
    Returns default for empty/unset values.
    """
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


def env_int(key: str, default: int) -> int:
    """Parse integer environment variable.

    Returns default if unset or invalid.
    Logs warning on invalid values.
    """
    val = os.getenv(key)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            logger.warning("Invalid integer for %s: %s, using default %d", key, val, default)
    return default


def utcnow() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


def normalize_utc_datetime(value: datetime | None) -> datetime | None:
    """Normalize a persisted UTC timestamp, including legacy naive values."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_utc_timestamp(value: str) -> str:
    """Normalize a persisted server-UTC ISO instant without rejecting legacy payloads."""
    if "T" not in value:
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    normalized = normalize_utc_datetime(parsed)
    assert normalized is not None
    return normalized.isoformat()


def parse_provider_timestamp(value: object) -> datetime | None:
    """Parse external Unix-seconds or aware ISO metadata without assigning a timezone."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            epoch = float(value)
        except OverflowError:
            return None
        try:
            return datetime.fromtimestamp(epoch, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else None
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def utcnow_isoformat() -> str:
    """Get current UTC time as ISO format string."""
    return utcnow().isoformat()
