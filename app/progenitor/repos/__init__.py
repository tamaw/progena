"""Declared-repository domain logic (git worktree/branch scanning)."""

from __future__ import annotations

from .scan import (
    RepoScanResult,
    WorkspaceSignal,
    aggregate_workspace_signal,
    scan_declared_repos,
    scan_repo,
)

__all__ = [
    "RepoScanResult",
    "WorkspaceSignal",
    "aggregate_workspace_signal",
    "scan_declared_repos",
    "scan_repo",
]
