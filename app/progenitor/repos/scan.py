"""Git worktree/branch scan domain logic for declared repos.json entries.

Pure, no Postgres access here - ``nodes/repo_scan.py`` is the heartbeat
adapter that reads declared entries, calls into this module, and persists the
result. Every git call is a read-only subprocess against the repo's working
tree; nothing here mutates a repository.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 10.0
_TREE_MAX_DEPTH = 2
_TREE_MAX_ENTRIES = 40
_TREE_SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache"}
_COMMIT_LIST_LIMIT = 5
_BRANCH_LIST_LIMIT = 25


async def _run_git(repo_path: Path, *args: str) -> tuple[int, str, str]:
    """Run one read-only git subcommand in ``repo_path``.

    Returns ``(returncode, stdout, stderr)``; never raises - subprocess and
    timeout failures come back as a non-zero code with the error in stderr so
    callers can treat every outcome uniformly.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_path),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        return 1, "", "git command timed out or failed to start"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", "replace").strip(),
        stderr.decode("utf-8", "replace").strip(),
    )


def _shallow_tree(repo_path: Path) -> str:
    """Render a depth-limited directory listing, VCS/build noise filtered."""
    lines: list[str] = []

    def walk(path: Path, prefix: str, depth: int) -> None:
        if depth > _TREE_MAX_DEPTH or len(lines) >= _TREE_MAX_ENTRIES:
            return
        try:
            entries = sorted(
                (entry for entry in path.iterdir() if entry.name not in _TREE_SKIP_NAMES),
                key=lambda entry: (not entry.is_dir(), entry.name.lower()),
            )
        except OSError:
            return
        for entry in entries:
            if len(lines) >= _TREE_MAX_ENTRIES:
                return
            lines.append(f"{prefix}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                walk(entry, prefix + "  ", depth + 1)

    walk(repo_path, "", 1)
    return "\n".join(lines)


@dataclass
class RepoScanResult:
    """One declared repo's derived git state, as of the last heartbeat tick."""

    exists: bool = False
    is_git_repo: bool = False
    branch: str | None = None
    dirty_count: int = 0
    commit_count: int = 0
    branch_count: int = 0
    commits_30d: int = 0
    last_commit: dict[str, str] | None = None
    branches: list[dict[str, Any]] = field(default_factory=list)
    commits: list[dict[str, str]] = field(default_factory=list)
    tree: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "is_git_repo": self.is_git_repo,
            "branch": self.branch,
            "dirty_count": self.dirty_count,
            "commit_count": self.commit_count,
            "branch_count": self.branch_count,
            "commits_30d": self.commits_30d,
            "last_commit": self.last_commit,
            "branches": self.branches,
            "commits": self.commits,
            "tree": self.tree,
            "error": self.error,
        }


async def scan_repo(local_path: str, *, worktree_dir: Path | None = None) -> RepoScanResult:
    """Scan one declared repo's on-disk git state.

    Never raises: a missing path or non-git directory just yields a mostly
    empty result (``exists``/``is_git_repo`` flags tell the caller why), so a
    repo declared before its worktree is cloned doesn't break the scan batch.
    """
    raw_path = Path(local_path) if local_path else Path()
    repo_path = raw_path if raw_path.is_absolute() else (worktree_dir or Path.cwd()) / raw_path
    result = RepoScanResult()
    if not local_path or not repo_path.is_dir():
        return result
    result.exists = True

    code, out, err = await _run_git(repo_path, "rev-parse", "--is-inside-work-tree")
    if code != 0 or out != "true":
        result.error = err or "not a git repository"
        return result
    result.is_git_repo = True

    _, branch_out, _ = await _run_git(repo_path, "branch", "--show-current")
    result.branch = branch_out or None

    _, status_out, _ = await _run_git(repo_path, "status", "--porcelain")
    result.dirty_count = len([line for line in status_out.splitlines() if line.strip()])

    _, count_out, _ = await _run_git(repo_path, "rev-list", "--count", "HEAD")
    result.commit_count = int(count_out) if count_out.isdigit() else 0

    _, refs_out, _ = await _run_git(
        repo_path, "for-each-ref", "--format=%(refname:short)", "refs/heads/"
    )
    branch_names = [line for line in refs_out.splitlines() if line.strip()]
    result.branch_count = len(branch_names)
    default_branch = result.branch or (branch_names[0] if branch_names else "")

    _, branch_list_out, _ = await _run_git(
        repo_path,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname:short)\t%(objectname:short)\t%(authorname)\t"
        "%(committerdate:relative)\t%(committerdate:iso-strict)",
        "refs/heads/",
    )
    branches: list[dict[str, Any]] = []
    for line in branch_list_out.splitlines()[:_BRANCH_LIST_LIMIT]:
        parts = line.split("\t")
        if len(parts) != 5:
            continue
        name, sha, author, relative_time, iso_time = parts
        ahead: str | None = None
        behind: str | None = None
        if default_branch and name != default_branch:
            code, counts_out, _ = await _run_git(
                repo_path, "rev-list", "--left-right", "--count", f"{default_branch}...{name}"
            )
            if code == 0 and "\t" in counts_out:
                behind_str, ahead_str = counts_out.split("\t", 1)
                ahead, behind = ahead_str.strip(), behind_str.strip()
        branches.append(
            {
                "name": name,
                "is_default": name == default_branch,
                "sha": sha,
                "author": author,
                "relative_time": relative_time,
                "updated_at": iso_time,
                "ahead": ahead,
                "behind": behind,
            }
        )
    result.branches = branches

    _, log_out, _ = await _run_git(
        repo_path, "log", f"-{_COMMIT_LIST_LIMIT}", "--format=%h\t%an\t%s\t%ar\t%aI"
    )
    commits: list[dict[str, str]] = []
    for line in log_out.splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        sha, author, message, relative_time, iso_time = parts
        commits.append(
            {
                "sha": sha,
                "author": author,
                "message": message,
                "relative_time": relative_time,
                "updated_at": iso_time,
            }
        )
    result.commits = commits
    result.last_commit = commits[0] if commits else None

    _, recent_out, _ = await _run_git(repo_path, "log", "--since=30.days", "--format=%h")
    result.commits_30d = len([line for line in recent_out.splitlines() if line.strip()])

    result.tree = _shallow_tree(repo_path)
    return result


@dataclass
class WorkspaceSignal:
    """Cross-repo aggregate shown in the Repositories page's workspace-signal row."""

    linked_repos: int = 0
    sync_health_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "linked_repos": self.linked_repos,
            "sync_health_pct": round(self.sync_health_pct, 1),
        }


def aggregate_workspace_signal(scans: dict[str, RepoScanResult]) -> WorkspaceSignal:
    """Aggregate cross-repo workspace signal from one scan batch."""
    resolved = [scan for scan in scans.values() if scan.exists and scan.is_git_repo]
    if not resolved:
        return WorkspaceSignal(linked_repos=len(scans), sync_health_pct=0.0)
    clean = sum(1 for scan in resolved if scan.dirty_count == 0)
    return WorkspaceSignal(
        linked_repos=len(scans),
        sync_health_pct=(clean / len(resolved)) * 100.0,
    )


async def scan_declared_repos(
    repos: dict[str, dict[str, Any]], *, worktree_dir: Path | None = None
) -> dict[str, RepoScanResult]:
    """Scan every declared repo entry concurrently."""
    repo_ids = list(repos)
    results = await asyncio.gather(
        *(
            scan_repo(str(repos[repo_id].get("local_path") or ""), worktree_dir=worktree_dir)
            for repo_id in repo_ids
        )
    )
    return dict(zip(repo_ids, results, strict=True))
