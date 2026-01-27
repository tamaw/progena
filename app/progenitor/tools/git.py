"""
Git Worktree Manager for Progenitor.

One worktree per work item for isolated build operations.
Includes automatic cleanup of orphaned worktrees on startup and failure.
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any
import uuid

from ..runtime.time_utils import format_timestamp, utcnow

logger = logging.getLogger(__name__)


class GitWorktreeManager:
    """Manages git worktrees for isolated build operations.

    Features:
    - Creates isolated worktrees per work item
    - Tracks worktree creation times for cleanup
    - Automatic cleanup of orphaned worktrees
    - Cleanup on failure and startup
    """

    # File to track worktree metadata
    METADATA_FILE = ".progenitor_worktree"

    def __init__(
        self,
        worktrees_dir: Path,
        repo_path: Optional[Path] = None,
        max_worktree_age_hours: int = 24,
        cleanup_on_init: bool = True,
    ):
        self.worktrees_dir = Path(worktrees_dir)
        self.repo_path = repo_path or Path.cwd()
        self.max_worktree_age_hours = max_worktree_age_hours

        if cleanup_on_init:
            # Schedule cleanup (non-blocking)
            asyncio.get_event_loop().call_soon(
                lambda: asyncio.create_task(self._startup_cleanup())
            )

    async def _startup_cleanup(self) -> None:
        """Run cleanup on startup to remove orphaned worktrees."""
        try:
            count = await self.cleanup_old_worktrees()
            if count > 0:
                logger.info("Startup cleanup removed %d orphaned worktree(s)", count)
        except Exception as e:
            logger.warning("Startup worktree cleanup failed: %s", e)

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
            logger.error("Failed to create worktree: %s", result['stderr'])
            raise RuntimeError(f"Failed to create worktree: {result['stderr']}")

        # Write metadata file for tracking
        await self._write_worktree_metadata(worktree_path, work_item_id, branch_name)

        logger.info("Created worktree at %s for work item %s", worktree_path, work_item_id)
        return worktree_path

    async def _write_worktree_metadata(
        self, worktree_path: Path, work_item_id: str, branch_name: str
    ) -> None:
        """Write metadata file to track worktree info."""
        metadata_path = worktree_path / self.METADATA_FILE
        metadata = {
            "work_item_id": work_item_id,
            "branch_name": branch_name,
            "created_at": utcnow().isoformat(),
        }
        try:
            import json
            with open(metadata_path, "w") as f:
                json.dump(metadata, f)
        except Exception as e:
            logger.warning("Failed to write worktree metadata: %s", e)

    async def _read_worktree_metadata(self, worktree_path: Path) -> Optional[dict]:
        """Read metadata from a worktree."""
        metadata_path = worktree_path / self.METADATA_FILE
        if not metadata_path.exists():
            return None
        try:
            import json
            with open(metadata_path, "r") as f:
                return json.load(f)
        except Exception:
            return None

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

    async def cleanup_old_worktrees(self, max_age_hours: Optional[int] = None) -> int:
        """Clean up worktrees older than max_age_hours.

        Args:
            max_age_hours: Override for max age (uses instance default if None)

        Returns:
            Number of worktrees cleaned up
        """
        max_age = max_age_hours or self.max_worktree_age_hours

        if not self.worktrees_dir.exists():
            return 0

        count = 0
        cutoff_time = utcnow() - timedelta(hours=max_age)

        for item in self.worktrees_dir.iterdir():
            if not item.is_dir():
                continue

            should_remove = False
            reason = ""

            # Check metadata for creation time
            metadata = await self._read_worktree_metadata(item)
            if metadata and "created_at" in metadata:
                try:
                    created_at = datetime.fromisoformat(metadata["created_at"])
                    if created_at.tzinfo is None:
                        # Assume UTC for naive datetimes
                        from datetime import timezone
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    if created_at < cutoff_time:
                        should_remove = True
                        reason = f"older than {max_age}h"
                except (ValueError, TypeError):
                    pass

            # Fallback: check directory modification time
            if not should_remove:
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    from datetime import timezone
                    mtime = mtime.replace(tzinfo=timezone.utc)
                    if mtime < cutoff_time:
                        should_remove = True
                        reason = f"mtime older than {max_age}h"
                except Exception:
                    pass

            if should_remove:
                try:
                    success = await self.remove_worktree(item)
                    if success:
                        count += 1
                        logger.info("Cleaned up worktree %s (%s)", item.name, reason)
                    else:
                        # Force remove if git worktree remove fails
                        await self._force_remove_directory(item)
                        count += 1
                        logger.info("Force-cleaned worktree %s (%s)", item.name, reason)
                except Exception as e:
                    logger.warning("Failed to clean up worktree %s: %s", item.name, e)

        return count

    async def cleanup_failed_worktree(self, worktree_path: Path) -> bool:
        """Clean up a worktree after a failed build.

        Called when a build fails to ensure the worktree doesn't get orphaned.

        Args:
            worktree_path: Path to the failed worktree

        Returns:
            True if cleanup succeeded
        """
        worktree_path = Path(worktree_path)
        if not worktree_path.exists():
            return True

        logger.info("Cleaning up failed worktree: %s", worktree_path)

        try:
            # Try git worktree remove first
            success = await self.remove_worktree(worktree_path)
            if success:
                return True

            # Force remove if git command fails
            await self._force_remove_directory(worktree_path)
            return True
        except Exception as e:
            logger.error("Failed to cleanup worktree %s: %s", worktree_path, e)
            return False

    async def _force_remove_directory(self, path: Path) -> None:
        """Force remove a directory when git worktree remove fails."""
        path = Path(path)
        if not path.exists():
            return

        # Run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: shutil.rmtree(path, ignore_errors=True))

        # Also prune git worktree list
        await self._run_git(["worktree", "prune"], cwd=self.repo_path)

    async def _run_git(
        self,
        args: list[str],
        cwd: Path,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Run a git command with timeout."""
        logger.info("GIT: Running git %s in %s", ' '.join(args), cwd)
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
                logger.info("GIT: Command completed with code %s", process.returncode)
                if stderr:
                    logger.warning("GIT stderr: %s", stderr.decode()[:200])
            except asyncio.TimeoutError:
                logger.error("GIT: Command timed out after %ss", timeout)
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
            logger.error("GIT: Exception running command: %s", e)
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
            }
