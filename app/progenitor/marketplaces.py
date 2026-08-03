"""Validation for the ``marketplaces`` state document (``marketplaces.json``).

Registers marketplace sources (GitHub repos to fetch ``.claude-plugin/marketplace.json``
from) the operator wants available for plugin installation. Distinct from
``plugins.json`` (see :mod:`app.progenitor.plugins.schema`), which tracks
*installed* plugins pulled from a marketplace's catalog.

The payload-native :func:`validate_marketplaces_payload` is the single
implementation both write paths converge on: the agent's virtual
``/config/marketplaces.json`` file (via :func:`validate_marketplaces_document`,
a thin ``json.loads`` wrapper) and the backend's ``/api/ui/marketplaces``
route (which already holds a parsed dict from FastAPI).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .schemas import check_entry_schema
from .utils import utcnow_isoformat

_MARKETPLACE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_marketplaces_payload(data: Any) -> dict[str, Any]:
    """Validate a decoded ``marketplaces`` payload (``{"marketplaces": {"<name>": {...}}}``).

    Shape: no other top-level keys; each key is a kebab-case marketplace name,
    each value validated against ``data/schemas/marketplace_entry.schema.v1.json``
    (the enforced floor — required ``source``, optional ``added_at``). Stamps
    ``added_at`` before the schema check if absent, so a fresh registration and
    a round-tripped edit both validate.
    """
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    extra_keys = set(data) - {"marketplaces"}
    if extra_keys:
        raise ValueError(f"unexpected top-level key(s): {', '.join(sorted(extra_keys))}")

    marketplaces = data.get("marketplaces")
    if not isinstance(marketplaces, dict):
        raise ValueError("missing 'marketplaces' object")

    for name, entry in marketplaces.items():
        if not _MARKETPLACE_NAME_RE.match(name):
            raise ValueError(
                f"marketplace name '{name}' must be kebab-case (lowercase, digits, hyphens)"
            )
        if not isinstance(entry, dict):
            raise ValueError(f"marketplace '{name}' must be an object")
        if "added_at" not in entry:
            entry["added_at"] = utcnow_isoformat()
        check_entry_schema(f"marketplace '{name}'", entry, "marketplace_entry")

    return data


def validate_marketplaces_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/marketplaces.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_marketplaces_payload(data)
