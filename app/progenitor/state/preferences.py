"""Shared validation for the ``preferences`` state document.

User profile preferences (display name, timezone) are edited from two places
that must never disagree on shape: the backend's ``PUT /api/ui/preferences``
route and the agent's ``/config/preferences.json`` virtual file. This module is
the single source of truth both import.
"""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..schemas import load_schema
from ..structured import validate_against_schema

PREFERENCES_DOC_KEY = "preferences"


def _check_schema(payload: dict[str, Any]) -> None:
    """Validate ``payload`` against ``preferences.schema.v1.json`` (the enforced floor).

    Mirrors ``state_backend._check_entry_schema``, but for the whole document
    rather than one entry in a collection — ``preferences.json`` has no
    array/map of entries to validate piecewise.
    """
    schema = load_schema("preferences")
    error = validate_against_schema(payload, schema)
    if error is not None:
        raise ValueError(f"preferences document does not conform to schema: {error}")


def default_preferences_payload() -> dict[str, Any]:
    """Return the empty-profile default (used to seed a fresh document)."""
    from ..schedule import default_timezone_name

    return {
        "display_name": "",
        "timezone": default_timezone_name(),
        "theme": "system",
        "nav_collapsed": False,
        "pinned_panel": None,
    }


def validate_preferences_payload(payload: Any) -> dict[str, Any]:
    """Validate a decoded preferences payload (display_name, timezone, plus the
    UI-only theme/nav_collapsed/pinned_panel settings).

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
    _check_schema(payload)

    display_name = payload.get("display_name", "")
    if not isinstance(display_name, str):
        raise ValueError("display_name must be a string")

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
        "timezone": timezone,
        "theme": payload.get("theme", "system"),
        "nav_collapsed": payload.get("nav_collapsed", False),
        "pinned_panel": payload.get("pinned_panel"),
    }


def validate_preferences_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/preferences.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"preferences.json must be valid JSON: {exc}") from exc
    return validate_preferences_payload(data)
