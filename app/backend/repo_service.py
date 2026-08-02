"""Backend access to the ``repos``/``repo_scan`` state documents.

Declared repositories live in the ``repos`` document under the
``progenitor.state`` namespace -- the same shared Postgres ``store`` table
Progenitor's own runtime reads and writes, and the same document the agent can
edit directly at ``/config/repos.json`` (see
``app/progenitor/agent/state_backend.py``). The backend reads/writes it with
straight SQL through ``PostgresDocumentStore``, same as ``marketplaces``/``mcp``.

Live git state (branches, commits, worktrees) lives in the separate
``repo_scan`` document, written only by the heartbeat's
``refresh_repo_scan_node`` (see ``app/progenitor/nodes/repo_scan.py``). The
backend only ever reads it here -- there is no write path from this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.progenitor.agent.state_backend import validate_repos_document
from app.progenitor.config import default_repos_config_payload
from app.progenitor.utils import utcnow_isoformat

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store

REPOS_DOC_KEY = "repos"
REPO_SCAN_DOC_KEY = "repo_scan"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class RepoNotFoundError(LookupError):
    """Raised when a declared repo id has no matching entry."""


def _slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "repo"


def _unique_repo_id(name: str, existing: dict[str, Any]) -> str:
    base = _slug(name)
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def validate_repos_payload(data: Any) -> dict[str, Any]:
    """Validate a repos payload (``{"repos": {"<id>": {...}}}``).

    Thin wrapper around the same validator the agent's ``/config/repos.json``
    writes go through, so both paths enforce one schema. Raises ``ValueError``
    (with a concise message) on any schema violation.
    """
    validated = validate_repos_document(json.dumps(data))
    if not isinstance(validated, dict):
        raise ValueError("top level must be a JSON object")
    return validated


def load_repos() -> dict[str, Any]:
    """Return the declared-repositories document."""
    default = default_repos_config_payload()
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, REPOS_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def save_repos(repos: dict[str, Any]) -> dict[str, Any]:
    """Persist the declared-repositories document and return it."""
    payload = validate_repos_payload({"repos": repos})
    get_default_document_store().save_document(PROGENITOR_STATE_PREFIX, REPOS_DOC_KEY, payload)
    return payload


def load_repo_scan() -> dict[str, Any]:
    """Return the heartbeat's last derived git-state scan, or an empty shell."""
    default: dict[str, Any] = {"scanned_at": None, "repos": {}, "workspace_signal": {}}
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, REPO_SCAN_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def create_repo(
    *,
    name: str,
    local_path: str,
    access: str,
    remote_url: str | None = None,
    summary: str | None = None,
    owner: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Add one declared repo entry. Returns ``(repo_id, entry)``."""
    if not name.strip():
        raise ValueError("name is required")
    if not local_path.strip():
        raise ValueError("local_path is required")
    if access not in {"read", "write"}:
        raise ValueError("access must be 'read' or 'write'")

    document = load_repos()
    repos = dict(document.get("repos") or {})
    repo_id = _unique_repo_id(name, repos)
    entry: dict[str, Any] = {
        "name": name.strip(),
        "local_path": local_path.strip(),
        "access": access,
        "created_at": utcnow_isoformat(),
        "updated_at": utcnow_isoformat(),
    }
    if remote_url:
        entry["remote_url"] = remote_url.strip()
    if summary:
        entry["summary"] = summary.strip()
    if owner:
        entry["owner"] = owner.strip()

    repos[repo_id] = entry
    saved = save_repos(repos)
    return repo_id, saved["repos"][repo_id]


def update_repo(repo_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Patch one declared repo entry's mutable fields."""
    document = load_repos()
    repos = dict(document.get("repos") or {})
    entry = repos.get(repo_id)
    if not isinstance(entry, dict):
        raise RepoNotFoundError(repo_id)

    merged = dict(entry)
    for field_name in ("name", "local_path", "access", "remote_url", "summary", "owner"):
        if field_name in updates:
            merged[field_name] = updates[field_name]
    repos[repo_id] = merged
    saved = save_repos(repos)
    entry_out: dict[str, Any] = saved["repos"][repo_id]
    return entry_out


def delete_repo(repo_id: str) -> bool:
    """Remove one declared repo entry. Returns ``False`` if it wasn't present."""
    document = load_repos()
    repos = dict(document.get("repos") or {})
    if repo_id not in repos:
        return False
    del repos[repo_id]
    save_repos(repos)
    return True


def merged_repo_rows() -> list[dict[str, Any]]:
    """Merge declared entries with their latest scan for the list view."""
    declared = load_repos().get("repos") or {}
    scan_doc = load_repo_scan()
    scans = scan_doc.get("repos") or {}
    rows: list[dict[str, Any]] = []
    for repo_id, entry in declared.items():
        if not isinstance(entry, dict):
            continue
        scan = scans.get(repo_id) if isinstance(scans, dict) else None
        rows.append(
            {
                "id": repo_id,
                "declared": entry,
                "scan": scan if isinstance(scan, dict) else None,
            }
        )
    return rows


def merged_repo_detail(repo_id: str) -> dict[str, Any]:
    """Merge one declared entry with its latest scan for the detail pane."""
    declared = load_repos().get("repos") or {}
    entry = declared.get(repo_id)
    if not isinstance(entry, dict):
        raise RepoNotFoundError(repo_id)
    scan_doc = load_repo_scan()
    scans = scan_doc.get("repos") or {}
    scan = scans.get(repo_id) if isinstance(scans, dict) else None
    return {
        "id": repo_id,
        "declared": entry,
        "scan": scan if isinstance(scan, dict) else None,
        "scanned_at": scan_doc.get("scanned_at"),
    }


def workspace_signal() -> dict[str, Any]:
    """Return the latest cross-repo workspace-signal aggregate."""
    signal = load_repo_scan().get("workspace_signal")
    return signal if isinstance(signal, dict) else {}
