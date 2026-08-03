"""Validation for the ``repos`` state document (``repos.json``).

Declares repositories the operator wants tracked (name, local path, access);
live git state is scanned separately by the heartbeat into ``repo_scan`` and
never lives here (see :mod:`app.progenitor.repos.scan`).

The payload-native :func:`validate_repos_payload` is the single
implementation both write paths converge on: the agent's virtual
``/config/repos.json`` file (via :func:`validate_repos_document`, a thin
``json.loads`` wrapper) and the backend's ``/api/ui/repos`` REST routes
(which already hold a parsed dict from FastAPI). Keeping one canonical
dict-in/dict-out function means both paths enforce the exact same rules
without a serialize/deserialize round trip between them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import check_entry_schema
from ..utils import utcnow_isoformat

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_repos_payload(data: Any) -> dict[str, Any]:
    """Validate a decoded ``repos`` payload (``{"repos": {"<id>": {...}}}``).

    Shape: no other top-level keys; each key is a kebab-case repo id, each
    value validated against ``data/schemas/repo_entry.schema.v1.json`` (the
    enforced floor — name/local_path/access, plus optional remote_url/summary/
    owner/created_at/updated_at). Stamps ``created_at``/``updated_at`` before
    the schema check, same as the schema permits, so edits that already carry
    them (round-tripped from storage) still validate.

    Raises ``ValueError`` (never a raw ``TypeError``/``KeyError``) on anything
    malformed, so both call sites surface one consistent error message.
    """
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    extra_keys = set(data) - {"repos"}
    if extra_keys:
        raise ValueError(f"unexpected top-level key(s): {', '.join(sorted(extra_keys))}")

    repos = data.get("repos")
    if not isinstance(repos, dict):
        raise ValueError("missing 'repos' object")

    for repo_id, entry in repos.items():
        if not _REPO_ID_RE.match(repo_id):
            raise ValueError(f"repo id '{repo_id}' must be kebab-case (lowercase, digits, hyphens)")
        if not isinstance(entry, dict):
            raise ValueError(f"repo '{repo_id}' must be an object")
        if "created_at" not in entry:
            entry["created_at"] = utcnow_isoformat()
        entry["updated_at"] = utcnow_isoformat()
        check_entry_schema(f"repo '{repo_id}'", entry, "repo_entry")

    return data


def validate_repos_document(content: str) -> dict[str, Any]:
    """Parse + validate ``/config/repos.json`` text into a normalized payload."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_repos_payload(data)
