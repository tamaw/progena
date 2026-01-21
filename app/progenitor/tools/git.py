"""
Git Worktree Manager for Progenitor.

One worktree per work item for isolated build operations.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import uuid

from ..runtime.time_utils import format_timestamp

logger = logging.getLogger(__name__)


class GitWorktreeManager:
    """Manages git worktrees for isolated build operations."""

    def __init__(self, worktrees_dir: Path, repo_path: Optional[Path] = None):
        self.worktrees_dir = Path(worktrees_dir)
        self.repo_path = repo_path or Path.cwd()

    async def create_worktree(
        self,
        work_item_id: str,
        branch_name: Optional[str] = None,
    ) -> Path:
        """Create a new worktree for a work item."""
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique worktree name
        timestamp = format_timestamp()
        worktree_name = f"{work_item_id}_{timestamp}"
        worktree_path = self.worktrees_dir / worktree_name

        # Generate branch name if not provided
        if not branch_name:
            branch_name = f"progenitor/{work_item_id}/{uuid.uuid4().hex[:8]}"

        # Create the worktree with a new branch
        result = await self._run_git(
            ["worktree", "add", "-b", branch_name, str(worktree_path)],
            cwd=self.repo_path
        )

        if result["returncode"] != 0:
            logger.error(f"Failed to create worktree: {result['stderr']}")
            raise RuntimeError(f"Failed to create worktree: {result['stderr']}")

        return worktree_path

    async def remove_worktree(self, worktree_path: Path) -> bool:
        """Remove a worktree after work is complete."""
        worktree_path = Path(worktree_path)

        if not worktree_path.exists():
            return True

        try:
            # Remove the worktree
            await self._run_git(
                ["worktree", "remove", "--force", str(worktree_path)],
                cwd=self.repo_path
            )
            return True
        except Exception:
            return False

    async def commit_changes(
        self,
        worktree_path: Path,
        message: str,
    ) -> Optional[str]:
        """Commit all changes in a worktree."""
        worktree_path = Path(worktree_path)

        # Stage all changes
        await self._run_git(["add", "-A"], cwd=worktree_path)

        # Check if there are changes to commit
        result = await self._run_git(
            ["status", "--porcelain"],
            cwd=worktree_path
        )

        if not result["stdout"].strip():
            return None  # Nothing to commit

        # Commit
        await self._run_git(
            ["commit", "-m", message],
            cwd=worktree_path
        )

        # Get commit hash
        result = await self._run_git(
            ["rev-parse", "HEAD"],
            cwd=worktree_path
        )

        return result["stdout"].strip()

    async def merge_to_main(
        self,
        worktree_path: Path,
        target_branch: str = "main",
    ) -> bool:
        """Merge worktree branch back to main."""
        worktree_path = Path(worktree_path)

        # Get current branch
        result = await self._run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path
        )
        source_branch = result["stdout"].strip()

        # Switch to main in main repo
        await self._run_git(["checkout", target_branch], cwd=self.repo_path)

        # Merge
        result = await self._run_git(
            ["merge", source_branch, "--no-ff", "-m", f"Merge {source_branch}"],
            cwd=self.repo_path
        )

        return result["returncode"] == 0

    async def get_diff(self, worktree_path: Path) -> str:
        """Get the current diff in a worktree."""
        worktree_path = Path(worktree_path)

        result = await self._run_git(["diff", "HEAD"], cwd=worktree_path)
        return result["stdout"]

    async def list_worktrees(self) -> list[dict[str, str]]:
        """List all active worktrees."""
        result = await self._run_git(
            ["worktree", "list", "--porcelain"],
            cwd=self.repo_path
        )

        worktrees = []
        current = {}

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

    async def cleanup_old_worktrees(self, max_age_hours: int = 24) -> int:
        """Clean up worktrees older than max_age_hours."""
        # This is a simple implementation that removes all progenitor worktrees
        # A more sophisticated version would track creation times

        if not self.worktrees_dir.exists():
            return 0

        count = 0
        for item in self.worktrees_dir.iterdir():
            if item.is_dir():
                try:
                    await self.remove_worktree(item)
                    count += 1
                except Exception:
                    pass

        return count

    async def _run_git(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Run a git command with timeout."""
        logger.info(f"GIT: Running git {' '.join(args)} in {cwd}")
        try:
            process = await asyncio.create_subprocess_exec(
                "git", *args,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
                logger.info(f"GIT: Command completed with code {process.returncode}")
                if stderr:
                    logger.warning(f"GIT stderr: {stderr.decode()[:200]}")
            except asyncio.TimeoutError:
                logger.error(f"GIT: Command timed out after {timeout}s")
                process.kill()
                return {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout} seconds",
                }

            return {
                "returncode": process.returncode,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
            }
        except Exception as e:
            logger.error(f"GIT: Exception running command: {e}")
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
            }
