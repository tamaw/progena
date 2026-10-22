"""Validation for ``repo:<id>`` documents (one declared repository each).

Declares a repository the operator wants tracked (name, local path, access);
live git state is scanned separately by the heartbeat into ``repo_scan`` and
never lives here (see :mod:`app.progenitor.repositories.scan`).

:func:`validate_repo_entry` is the single implementation both write paths
converge on: the agent's ``/config/repos/<id>.json`` files (via
:func:`validate_repo_document`) and the backend's ``/api/ui/repos`` routes.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.progenitor.documents.catalog import check_entry_schema
from app.progenitor.utils import utcnow_isoformat

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_repo_id(repo_id: str) -> str:
    """Return ``repo_id`` if it is kebab-case, else raise ``ValueError``."""
    if not isinstance(repo_id, str) or not _REPO_ID_RE.match(repo_id):
        raise ValueError(f"repo id '{repo_id}' must be kebab-case (lowercase, digits, hyphens)")
    return repo_id


def validate_repo_entry(
    repo_id: str, entry: Any, *, created_at: str | None = None
) -> dict[str, Any]:
    """Validate one repo entry against ``repo_entry.schema.v1.json``.

    Stamps ``updated_at`` on every write and ``created_at`` when absent;
    ``created_at`` (the stored value) wins over whatever the writer sent, so an
    edit never rewrites when the repo was declared. Raises ``ValueError`` on
    anything malformed.
    """
    validate_repo_id(repo_id)
    if not isinstance(entry, dict):
        raise ValueError(f"repo '{repo_id}' must be a JSON object")
    validated = dict(entry)
    now = utcnow_isoformat()
    validated["created_at"] = created_at or validated.get("created_at") or now
    validated["updated_at"] = now
    check_entry_schema(f"repo '{repo_id}'", validated, "repo_entry")
    return validated


def validate_repo_document(
    repo_id: str, content: str, *, created_at: str | None = None
) -> dict[str, Any]:
    """Parse + validate ``/config/repos/<id>.json`` text into a repo entry."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_repo_entry(repo_id, data, created_at=created_at)
