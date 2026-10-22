"""Shared structured log presentation."""

from typing import Any


def structured_log_message(log_name: str, payload: dict[str, Any]) -> str:
    """Extract a concise searchable message from one structured log payload."""
    for field in ("content", "message", "event", "id"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{log_name} event"
