"""Validation for the ``plugins`` state document (``plugins.json``).

Tracks *installed* plugins pulled from a marketplace's catalog. Distinct from
``marketplaces.json`` (see :mod:`app.progenitor.marketplaces`), which registers
the marketplace sources themselves.

The payload-native :func:`validate_plugins_payload` is the single
implementation both write paths converge on: the agent's virtual
``/config/plugins.json`` file (via :func:`validate_plugins_document`, a thin
``json.loads`` wrapper) and the backend's ``/api/ui/plugins`` routes (which
already hold a parsed dict from the reconciler/install flow).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import check_entry_schema

_PLUGIN_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")


def validate_plugins_payload(data: Any) -> dict[str, Any]:
    """Validate a decoded ``plugins`` payload (``{"plugins": {"<marketplace>/<plugin>": {...}}}``).

    Shape: no other top-level keys; each key is ``<marketplace>/<plugin>``,
    each value validated against ``data/schemas/plugin_entry.schema.v1.json``
    (the enforced floor, including ``source``'s discriminated oneOf --
    ``{"source": "github", "repo": ...}`` or ``{"source": "url"|"git-subdir",
    "url": ...}`` or a relative-path string; see
    ``app/progenitor/plugins/sources.py:_resolve_plugin_source`` for exactly
    what each source_type consumes). Runtime-owned fields (installed_version,
    installed_digest, available_version, status, status_message, skills,
    installed_at, last_checked) are accepted but never required; the
    reconciler manages them.
    """
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    extra_keys = set(data) - {"plugins"}
    if extra_keys:
        raise ValueError(f"unexpected top-level key(s): {', '.join(sorted(extra_keys))}")

    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        raise ValueError("missing 'plugins' object")

    for key, entry in plugins.items():
        if not _PLUGIN_KEY_RE.match(key):
            raise ValueError(f"plugin key '{key}' must look like <marketplace>/<plugin>")
        if not isinstance(entry, dict):
            raise ValueError(f"plugin '{key}' must be an object")
        check_entry_schema(f"plugin '{key}'", entry, "plugin_entry")

    return data


def validate_plugins_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/plugins.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_plugins_payload(data)
