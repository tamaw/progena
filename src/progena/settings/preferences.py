"""Shared validation for the ``preferences`` state document.

Instance preferences (user display name, timezone, the agent's display name)
are edited from two places that must never disagree on shape: the backend's
``PUT /api/ui/preferences`` route and the agent's ``/config/preferences.json``
virtual file. This module is the single source of truth both import.
"""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.progena.documents.catalog import check_entry_schema

PREFERENCES_DOC_KEY = "preferences"
DEFAULT_AGENT_NAME = "Progena"
AGENT_NAME_MAX_LENGTH = 32


def agent_display_name(preferences: Any) -> str:
    """The agent's configured display name, or the default when unset/invalid.

    Display and prompt text only; never an identifier (store keys, A2A caller,
    checkpoint agent names keep their fixed values).
    """
    value = preferences.get("agent_name") if isinstance(preferences, dict) else None
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > AGENT_NAME_MAX_LENGTH or not name.isprintable():
        return DEFAULT_AGENT_NAME
    return name


def default_preferences_payload() -> dict[str, Any]:
    """Return the empty-profile default (used to seed a fresh document)."""
    from src.progena.cron import default_timezone_name

    return {
        "display_name": "",
        "agent_name": DEFAULT_AGENT_NAME,
        "timezone": default_timezone_name(),
        "theme": "system",
        "nav_collapsed": False,
        "pinned_panel": None,
        "notification_sounds": False,
        "quick_chat_thread_id": None,
        "last_open_chat_thread_id": None,
    }


def validate_preferences_payload(payload: Any) -> dict[str, Any]:
    """Validate a decoded preferences payload (display_name, timezone, plus the
    UI-only theme/nav_collapsed/pinned_panel/notification_sounds settings).

    Raises ``ValueError`` (never a raw ``TypeError``/``KeyError``) on anything
    malformed, so both call sites can surface one consistent error message.
    Checks the JSON Schema floor first (object shape, allowed fields, required
    ``timezone``, correct types/enums for the UI fields), then layers the one
    rule a schema can't express: whether ``timezone`` is a real IANA zone name.
    Missing optional fields are normalized to their schema defaults, so every
    saved document is complete and self-describing for the agent to read back.
    """
    if not isinstance(payload, dict):
        raise ValueError("preferences document must be a JSON object")
    check_entry_schema("preferences document", payload, "preferences")

    display_name = payload.get("display_name", "")
    if not isinstance(display_name, str):
        raise ValueError("display_name must be a string")

    agent_name = payload.get("agent_name", "")
    if not isinstance(agent_name, str):
        raise ValueError("agent_name must be a string")
    agent_name = agent_name.strip() or DEFAULT_AGENT_NAME
    if len(agent_name) > AGENT_NAME_MAX_LENGTH:
        raise ValueError(f"agent_name must be at most {AGENT_NAME_MAX_LENGTH} characters")
    if not agent_name.isprintable():
        raise ValueError("agent_name must be a single line of printable text")

    timezone = payload.get("timezone")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("timezone is required and must be a non-empty string")
    timezone = timezone.strip()
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {timezone!r}") from exc

    return {
        "display_name": display_name.strip(),
        "agent_name": agent_name,
        "timezone": timezone,
        "theme": payload.get("theme", "system"),
        "nav_collapsed": payload.get("nav_collapsed", False),
        "pinned_panel": payload.get("pinned_panel"),
        "notification_sounds": payload.get("notification_sounds", False),
        "quick_chat_thread_id": payload.get("quick_chat_thread_id"),
        "last_open_chat_thread_id": payload.get("last_open_chat_thread_id"),
    }


def validate_preferences_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/preferences.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"preferences.json must be valid JSON: {exc}") from exc
    return validate_preferences_payload(data)
