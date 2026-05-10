"""
Utility functions for Progenitor.

Helpers for working with data structures and environment variable parsing.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")


def get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Get attribute from object or dict uniformly.

    Works with both:
    - Objects with attributes (e.g., Pydantic models, dataclasses)
    - Dictionaries

    Args:
        obj: The object or dict to access
        name: The attribute/key name
        default: Default value if not found

    Returns:
        The attribute value or default
    """
    if obj is None:
        return default

    if isinstance(obj, dict):
        return obj.get(name, default)

    return getattr(obj, name, default)


def get_attr_or_default(obj: Any, name: str, default: T) -> T:
    """Type-safe version of get_attr with guaranteed return type.

    Args:
        obj: The object or dict to access
        name: The attribute/key name
        default: Default value (determines return type)

    Returns:
        The attribute value cast to type of default, or default
    """
    value = get_attr(obj, name, default)
    if value is None:
        return default
    return cast(T, value)


def get_enum_value(obj: Any, default: str = "") -> str:
    """Extract string value from an enum or return string as-is.

    Args:
        obj: An enum instance or string
        default: Default if obj is None

    Returns:
        The string value
    """
    if obj is None:
        return default
    if hasattr(obj, "value"):
        return str(obj.value)
    return str(obj)


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


def env_float(key: str, default: float) -> float:
    """Parse float environment variable.

    Returns default if unset or invalid.
    Logs warning on invalid values.
    """
    val = os.getenv(key)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            logger.warning("Invalid float for %s: %s, using default %f", key, val, default)
    return default


def env_list(key: str, default: list[str] | None = None) -> list[str]:
    """Parse comma-separated list environment variable.

    Returns list of stripped non-empty strings.
    Returns default (or empty list) if unset.
    """
    val = os.getenv(key)
    if val:
        return [item.strip() for item in val.split(",") if item.strip()]
    return default or []


def utcnow() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(UTC)


def utcnow_isoformat() -> str:
    """Get current UTC time as ISO format string."""
    return utcnow().isoformat()


def format_timestamp(dt: datetime | None = None) -> str:
    """Format a datetime for file naming. Uses current time if not provided."""
    if dt is None:
        dt = utcnow()
    return dt.strftime("%Y%m%d_%H%M%S")
