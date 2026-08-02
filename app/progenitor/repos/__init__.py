"""Declared-repository domain logic (git worktree/branch scanning)."""

from __future__ import annotations

from .scan import (
    RepoScanResult,
    WorkspaceSignal,
    aggregate_workspace_signal,
    fetch_repo,
    scan_declared_repos,
    scan_repo,
)
from .schema import validate_repos_document, validate_repos_payload

__all__ = [
    "RepoScanResult",
    "WorkspaceSignal",
    "aggregate_workspace_signal",
    "fetch_repo",
    "scan_declared_repos",
    "scan_repo",
    "validate_repos_document",
    "validate_repos_payload",
]
