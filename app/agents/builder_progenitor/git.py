"""Git Worktree Manager for Builder Progenitor.

Independent implementation — no cross-agent imports.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


def _format_timestamp() -> str:
    """Generate a timestamp string for worktree naming."""
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


class GitWorktreeManager:
    """Manages git worktrees for isolated build operations."""

    METADATA_FILE = ".progenitor_worktree"

    def __init__(
        self,
        worktrees_dir: Path,
        repo_path: Path | None = None,
        max_worktree_age_hours: int = 24,
    ):
        self.worktrees_dir = Path(worktrees_dir)
        self.repo_path = repo_path or Path.cwd()
        self.max_worktree_age_hours = max_worktree_age_hours

    async def _run_git(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Run a git command with timeout."""
        logger.debug("GIT: git %s (cwd=%s)", " ".join(args), cwd)
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.CancelledError:
                process.kill()
                await process.communicate()
                raise
            except TimeoutError:
                process.kill()
                await process.communicate()
                return {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout}s",
                }
            return {
                "returncode": process.returncode,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
            }
        except Exception as e:
            logger.error("GIT: Exception running command: %s", e)
            return {"returncode": -1, "stdout": "", "stderr": str(e)}

    async def branch_exists(self, branch_name: str) -> bool:
        """Check if a branch exists in the repository."""
        result = await self._run_git(
            ["show-ref", "--verify", f"refs/heads/{branch_name}"],
            cwd=self.repo_path,
        )
        return bool(result["returncode"] == 0)

    async def is_branch_available(self, branch_name: str) -> dict[str, Any]:
        """Check if a branch name is available (not in use by any worktree).

        Returns dict with 'available' bool and 'used_by' path if not available.
        """
        worktrees = await self.list_worktrees()
        for wt in worktrees:
            wt_branch = wt.get("branch", "")
            # Branch refs look like "refs/heads/branch-name"
            if wt_branch.endswith(f"/{branch_name}") or wt_branch == branch_name:
                return {
                    "available": False,
                    "used_by": wt.get("path", "unknown"),
                    "branch": branch_name,
                }
        return {"available": True, "used_by": None, "branch": branch_name}

    async def list_worktrees(self) -> list[dict[str, str]]:
        """List all active worktrees with their paths and branches."""
        result = await self._run_git(
            ["worktree", "list", "--porcelain"],
            cwd=self.repo_path,
        )
        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}

        for line in result["stdout"].split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]

        if current:
            worktrees.append(current)

        return worktrees

    async def create_worktree(
        self,
        work_item_id: str,
        branch_name: str | None = None,
    ) -> Path:
        """Create a new worktree for a work item.

        Does NOT auto-retry on branch conflicts — the agent should handle that
        by calling is_branch_available first or choosing a different branch.
        """
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

        timestamp = _format_timestamp()
        worktree_name = f"{work_item_id}_{timestamp}"
        worktree_path = self.worktrees_dir / worktree_name

        selected_branch = branch_name or f"progenitor/{work_item_id}"

        if await self.branch_exists(selected_branch):
            command = ["worktree", "add", str(worktree_path), selected_branch]
        else:
            command = ["worktree", "add", "-b", selected_branch, str(worktree_path)]

        result = await self._run_git(command, cwd=self.repo_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"Failed to create worktree: {result['stderr']}")

        # Write metadata
        await self._write_metadata(worktree_path, work_item_id, selected_branch)

        logger.info("Created worktree at %s on branch %s", worktree_path, selected_branch)
        return worktree_path

    async def remove_worktree(self, worktree_path: str | Path) -> dict[str, Any]:
        """Remove a specific worktree by path."""
        worktree_path = Path(worktree_path)

        if not worktree_path.exists():
            return {"success": True, "message": "Worktree does not exist"}

        result = await self._run_git(
            ["worktree", "remove", "--force", str(worktree_path)],
            cwd=self.repo_path,
        )

        if result["returncode"] != 0:
            # Try force directory removal as fallback
            try:
                shutil.rmtree(worktree_path, ignore_errors=True)
                await self._run_git(["worktree", "prune"], cwd=self.repo_path)
                return {"success": True, "message": "Force removed worktree"}
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": True, "message": "Removed worktree"}

    async def prune_worktrees(self) -> dict[str, Any]:
        """Prune stale worktree references."""
        result = await self._run_git(["worktree", "prune"], cwd=self.repo_path)
        return {
            "success": result["returncode"] == 0,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    async def commit_changes(self, worktree_path: Path, message: str) -> str | None:
        """Stage and commit all changes in a worktree."""
        worktree_path = Path(worktree_path)

        await self._run_git(["add", "-A"], cwd=worktree_path)

        result = await self._run_git(["status", "--porcelain"], cwd=worktree_path)
        if not result["stdout"].strip():
            return None  # Nothing to commit

        before = await self._run_git(["rev-parse", "HEAD"], cwd=worktree_path)
        commit_result = await self._run_git(["commit", "-m", message], cwd=worktree_path)
        if commit_result["returncode"] != 0:
            detail = (commit_result["stderr"] or commit_result["stdout"]).strip()
            raise RuntimeError(f"Failed to commit changes: {detail or 'git commit failed'}")

        result = await self._run_git(["rev-parse", "HEAD"], cwd=worktree_path)
        if result["returncode"] != 0:
            return None

        before_sha = cast(str, before["stdout"]).strip() if before["returncode"] == 0 else ""
        after_sha = cast(str, result["stdout"]).strip()
        if before_sha and before_sha == after_sha:
            raise RuntimeError("git commit completed without advancing HEAD")

        return after_sha or None

    async def preflight_update(self, cwd: Path | None = None) -> dict[str, Any]:
        """Safely fetch and fast-forward a repo/worktree when possible.

        Returns structured readiness data instead of mutating implicitly:
        - fetches from remote with --prune
        - refuses to fast-forward when local changes are present
        - skips branches without upstreams
        - only updates via ff-only merge
        """
        workdir = Path(cwd) if cwd is not None else self.repo_path

        fetch = await self._run_git(["fetch", "--prune"], cwd=workdir)
        if fetch["returncode"] != 0:
            return {
                "success": False,
                "updated": False,
                "reason": "fetch_failed",
                "error": (fetch["stderr"] or fetch["stdout"]).strip(),
            }

        status = await self._run_git(["status", "--porcelain"], cwd=workdir)
        if status["returncode"] != 0:
            return {
                "success": False,
                "updated": False,
                "reason": "status_failed",
                "error": (status["stderr"] or status["stdout"]).strip(),
            }
        if status["stdout"].strip():
            return {"success": True, "updated": False, "reason": "local_changes"}

        branch_r = await self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=workdir)
        if branch_r["returncode"] != 0:
            return {"success": True, "updated": False, "reason": "no_branch"}
        branch = branch_r["stdout"].strip()

        upstream_r = await self._run_git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=workdir,
        )
        if upstream_r["returncode"] != 0:
            return {
                "success": True,
                "updated": False,
                "reason": "no_upstream",
                "branch": branch,
            }
        upstream = upstream_r["stdout"].strip()

        local_sha_r = await self._run_git(["rev-parse", "HEAD"], cwd=workdir)
        remote_sha_r = await self._run_git(["rev-parse", "@{u}"], cwd=workdir)
        if local_sha_r["returncode"] != 0 or remote_sha_r["returncode"] != 0:
            return {
                "success": True,
                "updated": False,
                "reason": "no_shas",
                "branch": branch,
                "upstream": upstream,
            }

        local_sha = local_sha_r["stdout"].strip()
        remote_sha = remote_sha_r["stdout"].strip()
        if local_sha == remote_sha:
            return {
                "success": True,
                "updated": False,
                "reason": "up_to_date",
                "branch": branch,
                "upstream": upstream,
                "rev": local_sha,
            }

        merge_r = await self._run_git(["merge", "--ff-only", "@{u}"], cwd=workdir)
        if merge_r["returncode"] != 0:
            return {
                "success": True,
                "updated": False,
                "reason": "non_ff",
                "branch": branch,
                "upstream": upstream,
                "local_rev": local_sha,
                "remote_rev": remote_sha,
            }

        new_sha_r = await self._run_git(["rev-parse", "HEAD"], cwd=workdir)
        new_sha = new_sha_r["stdout"].strip() if new_sha_r["returncode"] == 0 else ""
        return {
            "success": True,
            "updated": True,
            "reason": "fast_forwarded",
            "branch": branch,
            "upstream": upstream,
            "rev": new_sha or remote_sha,
            "previous_rev": local_sha,
        }

    async def _write_metadata(
        self, worktree_path: Path, work_item_id: str, branch_name: str
    ) -> None:
        """Write metadata file to track worktree info."""
        metadata_path = worktree_path / self.METADATA_FILE
        metadata = {
            "work_item_id": work_item_id,
            "branch_name": branch_name,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f)
        except Exception as e:
            logger.warning("Failed to write worktree metadata: %s", e)
