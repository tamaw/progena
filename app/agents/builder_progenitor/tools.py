"""Builder development tools."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from .config import BuilderConfig
from .copilot_acp import CopilotCLI
from .git import GitWorktreeManager
from .prompts import load_prompt, load_skill_context

logger = logging.getLogger(__name__)

_ACTIVE_TOOL_RUNTIMES: list[ToolRuntime] = []
PR_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+")


def _extract_pr_url(text: str) -> str:
    match = PR_URL_RE.search(text or "")
    return match.group(0) if match else ""


class GhCliInput(BaseModel):
    """Minimal schema for the generic builder gh tool."""

    model_config = ConfigDict(extra="forbid")

    worktree_path: str
    command: str
    timeout_seconds: int = 120


class GitCliInput(BaseModel):
    """Minimal schema for the generic builder git tool."""

    model_config = ConfigDict(extra="forbid")

    worktree_path: str
    command: str
    timeout_seconds: int = 120


# ---------------------------------------------------------------------------
# Tool runtime — shared state for all tools
# ---------------------------------------------------------------------------


class ToolRuntime:
    """Shared runtime for all builder tools."""

    def __init__(
        self,
        config: BuilderConfig,
        project_root: Path,
        log_dir: Path | None = None,
    ):
        repo_path = Path(os.getenv("PROGENITOR_WORKTREES_ROOT") or project_root)
        self.repo_path = repo_path
        self.config = config
        self.worktrees = GitWorktreeManager(
            worktrees_dir=config.storage.worktree_dir,
            repo_path=repo_path,
        )
        self.copilot = CopilotCLI(
            copilot_cmd=config.copilot.bin_path,
            model=config.copilot.model,
            flags=config.copilot.additional_flags,
            allowed_dirs=config.copilot.allowed_dirs,
            timeout=config.copilot.timeout_seconds,
            log_dir=log_dir,
        )

    async def close(self) -> None:
        """Release long-lived tool resources."""
        await self.copilot.close()

    async def run_command_args(
        self,
        cwd: str,
        args: list[str],
        timeout: int = 600,
    ) -> dict[str, Any]:
        """Run a subprocess without shell interpolation."""
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"success": False, "returncode": -1, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (out or b"").decode(errors="replace")[-12000:],
            "stderr": (err or b"").decode(errors="replace")[-12000:],
        }

    async def run_command(self, cwd: str, command: str, timeout: int = 600) -> dict[str, Any]:
        """Run a shell command and return result dict."""
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"success": False, "returncode": -1, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (out or b"").decode(errors="replace")[-12000:],
            "stderr": (err or b"").decode(errors="replace")[-12000:],
        }

    async def current_branch(self, cwd: str) -> str:
        r = await self.run_command(cwd, "git rev-parse --abbrev-ref HEAD", timeout=30)
        branch = (r.get("stdout") or "").strip()
        if not branch:
            raise RuntimeError(f"Could not determine branch: {r.get('stderr', '')}")
        return branch

    async def run_gh(self, cwd: str, args: list[str], timeout: int = 120) -> dict[str, Any]:
        """Run a gh CLI command in the worktree."""
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.CancelledError:
            proc.kill()
            await proc.communicate()
            raise
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"success": False, "returncode": -1, "stdout": "", "stderr": f"Timeout ({timeout}s)"}
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (out or b"").decode(errors="replace")[-12000:],
            "stderr": (err or b"").decode(errors="replace")[-12000:],
        }


async def shutdown_builder_tool_runtimes() -> None:
    """Close any active tool runtimes created by create_builder_tools()."""
    while _ACTIVE_TOOL_RUNTIMES:
        runtime = _ACTIVE_TOOL_RUNTIMES.pop()
        try:
            await runtime.close()
        except Exception:
            logger.exception("Failed to close builder tool runtime")


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def create_builder_tools(
    *,
    config: BuilderConfig,
    project_root: Path,
    log_dir: Path | None = None,
) -> list[StructuredTool]:
    """Create the full set of builder development tools."""
    rt = ToolRuntime(config, project_root, log_dir)
    _ACTIVE_TOOL_RUNTIMES.append(rt)

    # --- Worktree ---

    async def create_worktree(work_item_id: str, branch_name: str | None = None) -> dict[str, str]:
        """Create an isolated git worktree and return its path and branch.

        Runs a preflight update on the new worktree to fetch and safely fast-forward
        if possible, without overwriting uncommitted changes.
        """
        path = await rt.worktrees.create_worktree(work_item_id, branch_name=branch_name)
        branch = await rt.current_branch(str(path))
        # Run preflight on the newly created worktree
        try:
            await rt.worktrees.preflight_update(Path(path))
        except Exception:
            logger.exception("preflight: update for worktree %s failed", path)
        return {"worktree_path": str(path), "branch": branch}

    async def cleanup_worktrees(max_age_hours: int = 24) -> dict[str, Any]:
        """Remove worktrees older than max_age_hours."""
        wt_dir = config.storage.worktree_dir
        if not wt_dir.exists():
            return {"cleaned": 0}
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
        cleaned = 0
        for item in wt_dir.iterdir():
            if not item.is_dir():
                continue
            try:
                mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=UTC)
                if mtime < cutoff:
                    removed = await rt.worktrees.remove_worktree(item)
                    if removed.get("success"):
                        cleaned += 1
                        logger.info("Cleaned up worktree: %s", item.name)
            except OSError:
                pass
        return {"cleaned": cleaned}

    async def list_worktrees() -> dict[str, Any]:
        """List all active git worktrees with their paths and branches.

        Use this to see which branches are currently in use before creating
        a new worktree, or to find stale worktrees to remove.
        """
        worktrees = await rt.worktrees.list_worktrees()
        return {"worktrees": worktrees, "count": len(worktrees)}

    async def remove_worktree(worktree_path: str) -> dict[str, Any]:
        """Remove a specific worktree by its path.

        Use this to clean up a stale or conflicting worktree before creating
        a new one with the same branch name.
        """
        return await rt.worktrees.remove_worktree(worktree_path)

    async def check_branch_available(branch_name: str) -> dict[str, Any]:
        """Check if a branch name is available (not in use by any worktree).

        Call this before create_worktree to verify your chosen branch name
        is free. If not available, either remove the conflicting worktree
        or choose a different branch name (e.g., append -2, -3, etc.).
        """
        return await rt.worktrees.is_branch_available(branch_name)

    async def prune_worktrees() -> dict[str, Any]:
        """Prune stale worktree references from git.

        Run this to clean up git's internal worktree tracking after manually
        deleting worktree directories.
        """
        return await rt.worktrees.prune_worktrees()

    # --- Git Operations ---

    async def git_status(worktree_path: str) -> dict[str, Any]:
        """Get git status in a worktree."""
        return await rt.run_command(worktree_path, "git status --porcelain", timeout=30)

    async def git_diff(worktree_path: str, staged: bool = False) -> dict[str, Any]:
        """Get git diff in a worktree. Set staged=True for staged changes."""
        cmd = "git diff --cached" if staged else "git diff"
        return await rt.run_command(worktree_path, cmd, timeout=60)

    async def git_log(
        worktree_path: str, count: int = 10, oneline: bool = True
    ) -> dict[str, Any]:
        """Get recent git commits in a worktree."""
        fmt = "--oneline" if oneline else "--pretty=format:'%h %s (%an, %ar)'"
        return await rt.run_command(
            worktree_path, f"git log -{count} {fmt}", timeout=30
        )

    async def git_branch(
        worktree_path: str, create: str | None = None, delete: str | None = None
    ) -> dict[str, Any]:
        """List, create, or delete branches.

        - No args: list all branches
        - create='name': create a new branch
        - delete='name': delete a branch
        """
        if create:
            return await rt.run_command_args(
                worktree_path, ["git", "checkout", "-b", create], timeout=30
            )
        if delete:
            return await rt.run_command_args(
                worktree_path, ["git", "branch", "-D", delete], timeout=30
            )
        return await rt.run_command(worktree_path, "git branch -a", timeout=30)

    async def git_checkout(worktree_path: str, ref: str) -> dict[str, Any]:
        """Checkout a branch or commit in a worktree."""
        return await rt.run_command_args(worktree_path, ["git", "checkout", ref], timeout=60)

    async def git_fetch(worktree_path: str, prune: bool = False) -> dict[str, Any]:
        """Fetch from remote."""
        cmd = "git fetch --prune" if prune else "git fetch"
        return await rt.run_command(worktree_path, cmd, timeout=120)

    async def git_pull(worktree_path: str, rebase: bool = False) -> dict[str, Any]:
        """Pull from remote."""
        cmd = "git pull --rebase" if rebase else "git pull"
        return await rt.run_command(worktree_path, cmd, timeout=120)

    async def git_push(
        worktree_path: str,
        force: bool = False,
        set_upstream: bool = False,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Push to remote."""
        cmd = "git push"
        if force:
            cmd += " --force"
        if set_upstream and branch:
            cmd += f" --set-upstream origin {branch}"
        elif set_upstream:
            cmd += " --set-upstream origin HEAD"
        return await rt.run_command(worktree_path, cmd, timeout=180)

    async def git_reset(
        worktree_path: str, hard: bool = False, ref: str = "HEAD"
    ) -> dict[str, Any]:
        """Reset the worktree. Use hard=True to discard all changes."""
        cmd = f"git reset --hard {ref}" if hard else f"git reset {ref}"
        return await rt.run_command(worktree_path, cmd, timeout=30)

    async def git_stash(
        worktree_path: str, pop: bool = False, list_stash: bool = False
    ) -> dict[str, Any]:
        """Stash changes, pop stash, or list stashes."""
        if list_stash:
            return await rt.run_command(worktree_path, "git stash list", timeout=30)
        if pop:
            return await rt.run_command(worktree_path, "git stash pop", timeout=30)
        return await rt.run_command(worktree_path, "git stash", timeout=30)

    async def git_remote(worktree_path: str) -> dict[str, Any]:
        """List git remotes with URLs."""
        return await rt.run_command(worktree_path, "git remote -v", timeout=30)

    async def git_set_remote_url(
        worktree_path: str, url: str, remote: str = "origin"
    ) -> dict[str, Any]:
        """Set the URL for a git remote."""
        return await rt.run_command_args(
            worktree_path, ["git", "remote", "set-url", remote, url], timeout=30
        )

    async def git_cli(
        worktree_path: str,
        command: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Run any git CLI command in the worktree without the leading 'git'."""
        return await rt.run_command_args(
            worktree_path,
            ["git", *shlex.split(command)],
            timeout=timeout_seconds,
        )

    # --- GitHub CLI ---

    async def gh_auth_status() -> dict[str, Any]:
        """Check GitHub CLI authentication status."""
        return await rt.run_gh(str(rt.repo_path), ["auth", "status"], timeout=30)

    async def gh_auth_setup_git() -> dict[str, Any]:
        """Configure git to use GitHub CLI for authentication."""
        return await rt.run_gh(str(rt.repo_path), ["auth", "setup-git"], timeout=30)

    # --- Git Configuration ---

    async def configure_git(
        worktree_path: str,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> dict[str, Any]:
        """Configure git user identity in a worktree.

        Sets git user.name and user.email at the repo level. This ensures
        commits work even if subprocess environment doesn't inherit HOME
        (preventing access to ~/.gitconfig).

        If user_name or user_email are not provided, reads from environment
        variables GIT_AUTHOR_NAME and GIT_AUTHOR_EMAIL.
        """
        import os
        git_name = str(user_name or os.getenv("GIT_AUTHOR_NAME", "Progenitor Builder"))
        git_email = str(user_email or os.getenv("GIT_AUTHOR_EMAIL", "builder@progenitor.local"))

        name_result = await rt.run_command_args(
            worktree_path, ["git", "config", "user.name", git_name], timeout=30
        )
        if not name_result.get("success"):
            return {
                "success": False,
                "error": f"Failed to set user.name: {name_result.get('stderr', '')}",
            }

        email_result = await rt.run_command_args(
            worktree_path, ["git", "config", "user.email", git_email], timeout=30
        )
        if not email_result.get("success"):
            return {
                "success": False,
                "error": f"Failed to set user.email: {email_result.get('stderr', '')}",
            }

        return {
            "success": True,
            "user_name": git_name,
            "user_email": git_email,
        }

    async def setup_git_auth(
        worktree_path: str,
    ) -> dict[str, Any]:
        """Setup git credential helper to use gh CLI authentication.

        Configures git to use gh's credential helper for GitHub operations.
        This removes any conflicting or empty credential helper entries and
        ensures git push/pull operations can authenticate using GH_TOKEN.

        Call this if git operations fail with authentication errors like:
        - 'Invalid username or token'
        - 'Authentication failed'
        """
        # Use --replace-all to remove conflicting entries and set the correct one
        # Single global credential helper applies to all HTTPS URLs
        result = await rt.run_command(
            worktree_path,
            'git config --global --replace-all credential.helper "!gh auth git-credential"',
            timeout=30,
        )
        if not result.get("success"):
            return {
                "success": False,
                "error": f"Failed to configure credential helper: {result.get('stderr', result.get('stdout', 'Unknown error'))}",
            }

        return {
            "success": True,
            "message": "Git credential helper configured to use gh CLI for all HTTPS operations",
        }

    # --- Build ---

    async def copilot_build(
        work_item_id: str,
        worktree_path: str,
        prompt: str,
        target_path: str | None = None,
    ) -> dict[str, Any]:
        """Run Copilot in ACP mode to implement changes in the worktree."""
        result = await rt.copilot.build(
            work_item_id=work_item_id,
            worktree_path=Path(worktree_path),
            prompt=prompt,
            target_path=target_path,
        )
        return {
            "success": bool(result.get("success")),
            "summary": result.get("summary", ""),
            "stdout": str(result.get("stdout", ""))[-12000:],
            "stderr": str(result.get("stderr", ""))[-12000:],
            "diff": str(result.get("diff", ""))[-12000:],
        }

    async def render_copilot_prompt(
        request_text: str,
        task_name: str,
        work_item_id: str,
        branch_name: str,
        description: str,
        planned_at_utc: str,
        context_json: str = "{}",
        copilot_goal: str = "",
        success_criteria: str = "- none provided",
        validation_targets: str = "- pytest\n- pyright\n- ruff",
    ) -> dict[str, str]:
        """Render the Copilot ACP prompt from mounted prompt files."""
        prompt = load_prompt(
            "copilot_build.md",
            request_text=request_text.strip(),
            task_name=task_name.strip(),
            work_item_id=work_item_id.strip(),
            branch_name=branch_name.strip(),
            description=description.strip(),
            planned_at_utc=planned_at_utc.strip(),
            context_json=context_json.strip() or "{}",
            copilot_goal=copilot_goal.strip(),
            success_criteria=success_criteria.strip() or "- none provided",
            validation_targets=validation_targets.strip() or "- pytest\n- pyright\n- ruff",
            skill_context=load_skill_context(),
        )
        return {"prompt": prompt}

    # --- Validation ---

    async def validate_code(
        worktree_path: str,
        test_paths: str = "",
        fix_lint: bool = False,
    ) -> dict[str, Any]:
        """Run pytest, pyright, and ruff against the worktree to validate code quality."""
        results: dict[str, Any] = {}

        # pytest
        test_cmd = "python -m pytest -q --tb=short"
        if test_paths:
            test_cmd = f"python -m pytest {test_paths} -q --tb=short"
        r = await rt.run_command(worktree_path, test_cmd, timeout=300)
        results["pytest"] = {"passed": r["success"], "output": r["stdout"][-8000:]}

        # pyright
        r = await rt.run_command(worktree_path, "python -m pyright", timeout=120)
        results["pyright"] = {"passed": r["success"], "output": r["stdout"][-8000:]}

        # ruff
        ruff_cmd = "python -m ruff check --fix" if fix_lint else "python -m ruff check"
        r = await rt.run_command(worktree_path, ruff_cmd, timeout=60)
        results["ruff"] = {"passed": r["success"], "output": r["stdout"][-4000:]}

        results["all_passed"] = all(
            v["passed"] for v in results.values() if isinstance(v, dict) and "passed" in v
        )
        return results

    # --- Commands & search ---

    async def run_command(
        worktree_path: str,
        command: str,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run a shell command in the worktree."""
        return await rt.run_command(worktree_path, command, timeout=timeout_seconds)

    async def search(
        worktree_path: str,
        query: str,
        glob_pattern: str = "*.py",
    ) -> dict[str, Any]:
        """Search files in the worktree using ripgrep."""
        return await rt.run_command_args(
            worktree_path,
            ["rg", "-n", "--glob", glob_pattern, "--", query],
            timeout=120,
        )

    # --- Git & PR ---

    async def commit_changes(worktree_path: str, message: str) -> dict[str, Any]:
        """Stage and commit all changes in the worktree."""
        sha = await rt.worktrees.commit_changes(Path(worktree_path), message)
        return {"committed": sha is not None, "sha": sha or ""}

    async def create_pull_request(
        worktree_path: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> dict[str, Any]:
        """Push branch and create a pull request via GitHub CLI.

        Assumes git user config is already configured via configure_git tool.
        """
        branch = await rt.current_branch(worktree_path)
        push = await rt.run_command(
            worktree_path,
            f"git push --set-upstream origin {branch}",
            timeout=180,
        )
        if not push.get("success"):
            return {
                "success": False,
                "pushed": False,
                "url": "",
                "error": f"Push failed: {push.get('stderr', '')}".strip(),
            }

        pr = await rt.run_gh(
            worktree_path,
            [
                "pr",
                "create",
                "--title",
                title,
                "--body",
                body,
                "--base",
                base,
                "--head",
                branch,
            ],
            timeout=180,
        )
        pr_output = "\n".join(
            str(part).strip()
            for part in (pr.get("stdout"), pr.get("stderr"))
            if str(part or "").strip()
        ).strip()
        url = _extract_pr_url(pr_output)
        if not pr.get("success"):
            if url and "already exists" in pr_output.lower():
                return {
                    "success": True,
                    "pushed": True,
                    "existing_pr": True,
                    "url": url,
                    "error": "",
                    "output": pr_output[-4000:],
                }
            return {
                "success": False,
                "pushed": True,
                "url": "",
                "error": (pr.get("stderr") or pr.get("stdout") or "gh pr create failed").strip(),
                "output": pr_output[-4000:],
            }

        if not url:
            return {
                "success": False,
                "pushed": True,
                "url": "",
                "error": f"gh pr create succeeded but no PR URL found in output: {pr_output[:500]}",
                "output": pr_output[-4000:],
            }

        return {
            "success": True,
            "pushed": True,
            "existing_pr": False,
            "url": url,
            "error": "",
            "output": pr_output[-4000:],
        }

    async def gh_cli(
        worktree_path: str,
        command: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Run any gh CLI command in the worktree (without the leading 'gh')."""
        return await rt.run_gh(worktree_path, shlex.split(command), timeout=timeout_seconds)

    # --- Tool registration ---

    return [
        # --- Worktree Management ---
        StructuredTool.from_function(
            coroutine=create_worktree, name="create_worktree",
            description="Create an isolated git worktree for the task.",
        ),
        StructuredTool.from_function(
            coroutine=cleanup_worktrees, name="cleanup_worktrees",
            description="Remove worktrees older than max_age_hours.",
        ),
        StructuredTool.from_function(
            coroutine=list_worktrees, name="list_worktrees",
            description="List all active git worktrees with their paths and branches.",
        ),
        StructuredTool.from_function(
            coroutine=remove_worktree, name="remove_worktree",
            description="Remove a specific worktree by path. Use to clean up stale/conflicting worktrees.",
        ),
        StructuredTool.from_function(
            coroutine=check_branch_available, name="check_branch_available",
            description="Check if a branch name is available (not in use by another worktree).",
        ),
        StructuredTool.from_function(
            coroutine=prune_worktrees, name="prune_worktrees",
            description="Prune stale worktree references from git.",
        ),
        # --- Git Operations ---
        StructuredTool.from_function(
            coroutine=git_status, name="git_status",
            description="Get git status in a worktree.",
        ),
        StructuredTool.from_function(
            coroutine=git_diff, name="git_diff",
            description="Get git diff. Set staged=True for staged changes.",
        ),
        StructuredTool.from_function(
            coroutine=git_log, name="git_log",
            description="Get recent git commits.",
        ),
        StructuredTool.from_function(
            coroutine=git_branch, name="git_branch",
            description="List, create, or delete branches.",
        ),
        StructuredTool.from_function(
            coroutine=git_checkout, name="git_checkout",
            description="Checkout a branch or commit.",
        ),
        StructuredTool.from_function(
            coroutine=git_fetch, name="git_fetch",
            description="Fetch from remote.",
        ),
        StructuredTool.from_function(
            coroutine=git_pull, name="git_pull",
            description="Pull from remote.",
        ),
        StructuredTool.from_function(
            coroutine=git_push, name="git_push",
            description="Push to remote. Supports force and set_upstream options.",
        ),
        StructuredTool.from_function(
            coroutine=git_reset, name="git_reset",
            description="Reset the worktree. Use hard=True to discard all changes.",
        ),
        StructuredTool.from_function(
            coroutine=git_stash, name="git_stash",
            description="Stash changes, pop stash, or list stashes.",
        ),
        StructuredTool.from_function(
            coroutine=git_remote, name="git_remote",
            description="List git remotes with URLs.",
        ),
        StructuredTool.from_function(
            coroutine=git_set_remote_url, name="git_set_remote_url",
            description="Set the URL for a git remote.",
        ),
        StructuredTool.from_function(
            coroutine=git_cli,
            args_schema=GitCliInput,
            name="git_cli",
            description=(
                "Run any git command without the leading 'git'. "
                "Use command='status', 'checkout my-branch', or 'checkout --help' for discovery."
            ),
        ),
        # --- Git Config ---
        StructuredTool.from_function(
            coroutine=configure_git, name="configure_git",
            description="Configure git user identity in a worktree. Must be called before commit or push operations.",
        ),
        StructuredTool.from_function(
            coroutine=setup_git_auth, name="setup_git_auth",
            description="Setup gh CLI authentication for git operations. Call this if git push fails with authentication errors.",
        ),
        # --- GitHub CLI ---
        StructuredTool.from_function(
            coroutine=gh_auth_status, name="gh_auth_status",
            description="Check GitHub CLI authentication status.",
        ),
        StructuredTool.from_function(
            coroutine=gh_auth_setup_git, name="gh_auth_setup_git",
            description="Configure git to use GitHub CLI for authentication.",
        ),
        StructuredTool.from_function(
            coroutine=gh_cli,
            args_schema=GhCliInput,
            name="gh_cli",
            description=(
                "Run any GitHub CLI command without the leading 'gh'. "
                "Use command='pr view 123' or command='help pr create' for discovery."
            ),
        ),
        # --- Build & Validation ---
        StructuredTool.from_function(
            coroutine=render_copilot_prompt, name="render_copilot_prompt",
            description="Render the Copilot ACP prompt from structured task metadata and mounted prompt files.",
        ),
        StructuredTool.from_function(
            coroutine=copilot_build, name="copilot_build",
            description="Run Copilot ACP to implement changes in a worktree.",
        ),
        StructuredTool.from_function(
            coroutine=validate_code, name="validate_code",
            description="Run pytest, pyright, and ruff to validate code quality.",
        ),
        StructuredTool.from_function(
            coroutine=run_command, name="run_command",
            description="Run a shell command in the worktree.",
        ),
        StructuredTool.from_function(
            coroutine=search, name="search",
            description="Search files in the worktree using ripgrep.",
        ),
        # --- Commit & PR ---
        StructuredTool.from_function(
            coroutine=commit_changes, name="commit_changes",
            description="Stage and commit all changes in the worktree.",
        ),
        StructuredTool.from_function(
            coroutine=create_pull_request, name="create_pull_request",
            description="Push branch and create a GitHub pull request.",
        ),
    ]
