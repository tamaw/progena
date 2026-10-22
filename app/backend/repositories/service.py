"""Backend access to ``repo:<id>`` documents and the ``repo_scan`` document.

Each declared repository is one ``repo:<id>`` document under the
``progenitor.state`` namespace -- the same shared Postgres ``store`` table
Progenitor's own runtime reads and writes, and the same documents the agent
edits as ``/config/repos/<id>.json`` (see ``app/progenitor/agent/filesystem/``).
Every write touches one repo's row under that key's lock, so concurrent edits
of different repos never overwrite each other, and each create/update/delete
is its own typed ``repo`` document event.

Live git state (branches, commits, worktrees) lives in the separate
``repo_scan`` document. The heartbeat's ``refresh_repo_scan_node`` (see
``app/progenitor/orchestration/nodes/repo_scan.py``) is the periodic, local-only writer --
it never does network I/O, so it can't tell a stale clone from a fresh one.
``fetch_and_rescan_repo`` below is the on-demand counterpart: it runs
``git fetch`` against one repo (real network I/O, deliberately never part of
the heartbeat batch) and merges a fresh single-repo scan into the same
document, so a manual "Fetch" click doesn't wait out the heartbeat interval.
Both writers persist through the same ``save_document`` call the heartbeat
uses, so the runtime's cached copy is invalidated (see ``_on_store_change`` in
``app/progenitor/runtime/progenitor.py``).
"""

from __future__ import annotations

import re
from typing import Any

from app.backend.storage.documents import PROGENITOR_STATE_PREFIX, get_default_document_store
from app.progenitor.config import get_config
from app.progenitor.documents.kinds import REPO_KEY_PREFIX
from app.progenitor.repositories.scan import (
    RepoScanResult,
    aggregate_workspace_signal,
    fetch_repo,
    scan_repo,
)
from app.progenitor.repositories.schema import validate_repo_entry
from app.progenitor.utils import utcnow_isoformat

REPO_SCAN_DOC_KEY = "repo_scan"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class RepoNotFoundError(LookupError):
    """Raised when a declared repo id has no matching entry."""


class _RepoIdTaken(Exception):
    """A create's candidate id already belongs to another repo."""


def _slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "repo"


def repo_key(repo_id: str) -> str:
    return f"{REPO_KEY_PREFIX}{repo_id}"


def load_repo(repo_id: str) -> dict[str, Any] | None:
    """Return one declared repo entry, or ``None`` if it isn't declared."""
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, repo_key(repo_id), None
    )
    payload = document.payload if document is not None else None
    return payload if isinstance(payload, dict) else None


def load_repos() -> dict[str, dict[str, Any]]:
    """Return every declared repo entry, keyed by repo id."""
    return {
        key.removeprefix(REPO_KEY_PREFIX): document.payload
        for key, document in get_default_document_store().list_documents(
            PROGENITOR_STATE_PREFIX, REPO_KEY_PREFIX
        )
        if isinstance(document.payload, dict)
    }


def load_repo_scan() -> dict[str, Any]:
    """Return the heartbeat's last derived git-state scan, or an empty shell."""
    default: dict[str, Any] = {"scanned_at": None, "repos": {}, "workspace_signal": {}}
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, REPO_SCAN_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


class RepoFetchError(RuntimeError):
    """Raised when ``git fetch`` fails for a declared repo."""


async def fetch_and_rescan_repo(repo_id: str) -> dict[str, Any]:
    """Run ``git fetch`` against one declared repo, then merge a fresh scan.

    On-demand counterpart to the heartbeat's periodic local-only scan: does
    real network I/O (deliberately never part of the heartbeat batch, see
    module docstring), then re-scans just this repo and writes the merged
    result into the same ``repo_scan`` document the heartbeat maintains, so
    a manual click doesn't wait out the heartbeat interval to show whether
    the repo is stale relative to its upstream.
    """
    entry = load_repo(repo_id)
    if entry is None:
        raise RepoNotFoundError(repo_id)
    local_path = entry.get("local_path") or ""
    worktree_dir = get_config().storage.worktree_dir

    error = await fetch_repo(local_path, worktree_dir=worktree_dir)
    if error:
        raise RepoFetchError(error)

    scan_result = await scan_repo(local_path, worktree_dir=worktree_dir)
    scan_doc = load_repo_scan()
    previous_scans = scan_doc.get("repos") or {}
    # Reconstruct every other repo's last scan as a RepoScanResult (to_dict()'s
    # keys mirror the dataclass fields exactly) so the real
    # aggregate_workspace_signal -- the same one the heartbeat uses -- can run
    # over the merged set instead of duplicating its aggregation logic here.
    scans: dict[str, RepoScanResult] = {}
    for other_id, other_scan in previous_scans.items():
        if other_id == repo_id or not isinstance(other_scan, dict):
            continue
        try:
            scans[other_id] = RepoScanResult(**other_scan)
        except TypeError:
            continue
    scans[repo_id] = scan_result
    signal = aggregate_workspace_signal(scans)

    payload = {
        "scanned_at": utcnow_isoformat(),
        "repos": {rid: result.to_dict() for rid, result in scans.items()},
        "workspace_signal": signal.to_dict(),
    }
    get_default_document_store().save_document(PROGENITOR_STATE_PREFIX, REPO_SCAN_DOC_KEY, payload)
    return scan_result.to_dict()


def create_repo(
    *,
    name: str,
    local_path: str,
    access: str,
    remote_url: str | None = None,
    summary: str | None = None,
    owner: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Add one declared repo entry. Returns ``(repo_id, entry)``.

    The id is the name's slug, suffixed ``-2``, ``-3``... until free; each
    candidate is claimed under its key's lock, so two concurrent creates of
    the same name get different ids.
    """
    if not name.strip():
        raise ValueError("name is required")
    if not local_path.strip():
        raise ValueError("local_path is required")
    if access not in {"read", "write"}:
        raise ValueError("access must be 'read' or 'write'")

    entry: dict[str, Any] = {
        "name": name.strip(),
        "local_path": local_path.strip(),
        "access": access,
    }
    if remote_url:
        entry["remote_url"] = remote_url.strip()
    if summary:
        entry["summary"] = summary.strip()
    if owner:
        entry["owner"] = owner.strip()

    base = _slug(name)
    store = get_default_document_store()
    for suffix in range(1, 1000):
        repo_id = base if suffix == 1 else f"{base}-{suffix}"

        def claim(existing: Any, repo_id: str = repo_id) -> dict[str, Any]:
            if existing is not None:
                raise _RepoIdTaken
            return validate_repo_entry(repo_id, entry)

        try:
            saved = store.mutate_document(PROGENITOR_STATE_PREFIX, repo_key(repo_id), claim)
        except _RepoIdTaken:
            continue
        return repo_id, saved
    raise ValueError(f"no free repo id for name {name!r}")


def update_repo(repo_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Patch one declared repo entry's mutable fields."""

    def patch(existing: Any) -> dict[str, Any]:
        if not isinstance(existing, dict):
            raise RepoNotFoundError(repo_id)
        merged = dict(existing)
        for field_name in ("name", "local_path", "access", "remote_url", "summary", "owner"):
            if field_name in updates:
                merged[field_name] = updates[field_name]
        return validate_repo_entry(repo_id, merged, created_at=existing.get("created_at"))

    saved: dict[str, Any] = get_default_document_store().mutate_document(
        PROGENITOR_STATE_PREFIX, repo_key(repo_id), patch
    )
    return saved


def delete_repo(repo_id: str) -> bool:
    """Remove one declared repo entry. Returns ``False`` if it wasn't present."""
    if load_repo(repo_id) is None:
        return False
    get_default_document_store().delete_document(PROGENITOR_STATE_PREFIX, repo_key(repo_id))
    return True


def merged_repo_rows() -> list[dict[str, Any]]:
    """Merge declared entries with their latest scan for the list view."""
    declared = load_repos()
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
    entry = load_repo(repo_id)
    if entry is None:
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
