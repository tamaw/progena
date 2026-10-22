"""Declared-repository domain logic (git worktree/branch scanning)."""

from __future__ import annotations

from app.progenitor.repositories.scan import (
    RepoScanResult,
    WorkspaceSignal,
    aggregate_workspace_signal,
    fetch_repo,
    scan_declared_repos,
    scan_repo,
)
from app.progenitor.repositories.schema import (
    validate_repo_document,
    validate_repo_entry,
    validate_repo_id,
)

__all__ = [
    "RepoScanResult",
    "WorkspaceSignal",
    "aggregate_workspace_signal",
    "fetch_repo",
    "scan_declared_repos",
    "scan_repo",
    "validate_repo_document",
    "validate_repo_entry",
    "validate_repo_id",
]
