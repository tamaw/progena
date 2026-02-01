"""
Environment variable parsing utilities for Backend.
"""

import logging
import os

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
