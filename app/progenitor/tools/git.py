"""
Git Worktree Manager for Progenitor.

One worktree per work item for isolated build operations.
Includes automatic cleanup of orphaned worktrees on startup and failure.
"""

import asyncio
import logging
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ..runtime.time_utils import format_timestamp, utcnow
from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class GitWorktreeManager:
    """Manages git worktrees for isolated build operations.

    Features:
    - Creates isolated worktrees per work item
    - Tracks worktree creation times for cleanup
    - Automatic cleanup of orphaned worktrees
    - Cleanup on failure and startup

    CR-003: Uses initialization lock to prevent race condition between
    startup cleanup and first work item creation.
    """

    # File to track worktree metadata
    METADATA_FILE = ".progenitor_worktree"

    def __init__(
        self,
        worktrees_dir: Path,
        repo_path: Path | None = None,
        max_worktree_age_hours: int = 24,
        cleanup_on_init: bool = True,
    ):
        self.worktrees_dir = Path(worktrees_dir)
        self.repo_path = repo_path or Path.cwd()
        self.max_worktree_age_hours = max_worktree_age_hours

        # CR-003: Lock to prevent race between cleanup and worktree creation
        self._init_lock = asyncio.Lock()
        self._cleanup_complete = asyncio.Event()
        self._cleanup_task: asyncio.Task | None = None

        if cleanup_on_init:
            # CR-003: Schedule cleanup with proper synchronization
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._startup_cleanup())
            except RuntimeError:
                # No running loop - defer cleanup to first operation
                pass

    async def _startup_cleanup(self) -> None:
        """Run cleanup on startup to remove orphaned worktrees.

        CR-003: Uses lock to prevent race condition with create_worktree.
        """
        async with self._init_lock:
            try:
                count = await self.cleanup_old_worktrees()
                if count > 0:
                    logger.info("Startup cleanup removed %d orphaned worktree(s)", count)
            except Exception as e:
                logger.warning("Startup worktree cleanup failed: %s", e)
            finally:
                # CR-003: Signal that cleanup is complete
                self._cleanup_complete.set()

    async def create_worktree(
        self,
        work_item_id: str,
        branch_name: str | None = None,
    ) -> Path:
        """Create a new worktree for a work item.

        CR-003: Waits for startup cleanup to complete before creating.
        """
        # CR-003: Wait for any pending startup cleanup to complete
        # This prevents race condition where cleanup could delete a new worktree
        if self._cleanup_task and not self._cleanup_complete.is_set():
            try:
                await asyncio.wait_for(self._cleanup_complete.wait(), timeout=30.0)
            except TimeoutError:
                logger.warning("Startup cleanup timed out, proceeding with worktree creation")

        async with self._init_lock:
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

    async def _read_worktree_metadata(self, worktree_path: Path) -> dict | None:
        """Read metadata from a worktree."""
        import json
        metadata_path = worktree_path / self.METADATA_FILE
        if not metadata_path.exists():
            return None
        try:
            with open(metadata_path) as f:
                return cast(dict, json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to read worktree metadata from %s: %s", metadata_path, e)
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
        except (OSError, RuntimeError) as e:
            logger.warning("Failed to remove worktree %s: %s", worktree_path, e)
            return False

    async def commit_changes(
        self,
        worktree_path: Path,
        message: str,
    ) -> str | None:
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

        return cast(str, result["stdout"].strip())

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

        return cast(bool, result["returncode"] == 0)

    async def get_diff(self, worktree_path: Path) -> str:
        """Get the current diff in a worktree."""
        worktree_path = Path(worktree_path)

        result = await self._run_git(["diff", "HEAD"], cwd=worktree_path)
        return cast(str, result["stdout"])

    async def list_worktrees(self) -> list[dict[str, str]]:
        """List all active worktrees."""
        result = await self._run_git(
            ["worktree", "list", "--porcelain"],
            cwd=self.repo_path
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

    async def cleanup_old_worktrees(self, max_age_hours: int | None = None) -> int:
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
                        created_at = created_at.replace(tzinfo=UTC)
                    if created_at < cutoff_time:
                        should_remove = True
                        reason = f"older than {max_age}h"
                except (ValueError, TypeError):
                    pass

            # Fallback: check directory modification time
            if not should_remove:
                try:
                    mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    mtime = mtime.replace(tzinfo=UTC)
                    if mtime < cutoff_time:
                        should_remove = True
                        reason = f"mtime older than {max_age}h"
                except (OSError, ValueError) as e:
                    logger.debug("Failed to check mtime for %s: %s", item.name, e)

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
            except TimeoutError:
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


class GitTool(BaseSystemTool):
    """Report git/worktree status used by the build system."""

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "List active git worktrees (used for isolated build operations)"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        project_root = Path(__file__).parent.parent.parent.parent
        worktrees_dir = project_root / "worktrees"
        manager = GitWorktreeManager(
            worktrees_dir=worktrees_dir,
            repo_path=project_root,
            cleanup_on_init=False,
        )
        worktrees = await manager.list_worktrees()
        return ToolResult.ok({
            "repo_path": str(project_root),
            "worktrees_dir": str(worktrees_dir),
            "worktrees": worktrees,
            "count": len(worktrees),
        })
