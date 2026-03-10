"""Builder development tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from app.agents.builder_agent.tools.git import GitWorktreeManager

from .config import BuilderConfig
from .copilot_acp import CopilotCLI

logger = logging.getLogger(__name__)

_ACTIVE_TOOL_RUNTIMES: list[ToolRuntime] = []


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _derive_work_item_suffix(context_json: str | None) -> str:
    """Extract a stable identifier from context JSON for branch naming."""
    if not context_json:
        return "manual"
    try:
        ctx = json.loads(context_json)
    except json.JSONDecodeError:
        return "manual"
    if not isinstance(ctx, dict):
        return "manual"
    for key in ("a2a_task_id", "task_id", "run_id"):
        val = ctx.get(key)
        if isinstance(val, str) and val.strip():
            return "".join(c for c in val.lower() if c.isalnum())[:16]
    return "manual"


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

    async def run_command(self, cwd: str, command: str, timeout: int = 600) -> dict[str, Any]:
        """Run a shell command and return result dict."""
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
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
        """Create an isolated git worktree and return its path and branch."""
        path = await rt.worktrees.create_worktree(work_item_id, branch_name=branch_name)
        branch = await rt.current_branch(str(path))
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
                    shutil.rmtree(item, ignore_errors=True)
                    cleaned += 1
                    logger.info("Cleaned up worktree: %s", item.name)
            except OSError:
                pass
        return {"cleaned": cleaned}

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
        return await rt.run_command(
            worktree_path,
            f'rg -n --glob "{glob_pattern}" -- "{query}"',
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

        Ensures git user config is set at repo level before push, as subprocess
        environment may not inherit HOME needed for global ~/.gitconfig access.
        """
        # Ensure git user config is set at repo level before pushing.
        # Required because subprocess may not inherit HOME, preventing git from
        # reading ~/.gitconfig. Setting repo-level config ensures push works.
        import os
        git_name = os.getenv("GIT_AUTHOR_NAME", "Progenitor Builder")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "builder@progenitor.local")
        await rt.run_command(worktree_path, f"git config user.name '{git_name}'", timeout=30)
        await rt.run_command(worktree_path, f"git config user.email '{git_email}'", timeout=30)

        branch = await rt.current_branch(worktree_path)
        push = await rt.run_command(
            worktree_path,
            f"git push --set-upstream origin {branch}",
            timeout=180,
        )
        if not push.get("success"):
            return {
                "success": False,
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
        if not pr.get("success"):
            return {
                "success": False,
                "url": "",
                "error": (pr.get("stderr") or pr.get("stdout") or "gh pr create failed").strip(),
            }

        pr_output = (pr.get("stdout") or "").strip()
        url = ""
        for line in pr_output.splitlines():
            line = line.strip()
            if line.startswith("https://github.com/") and "/pull/" in line:
                url = line
                break

        if not url:
            return {
                "success": False,
                "url": "",
                "error": f"gh pr create succeeded but no PR URL found in output: {pr_output[:500]}",
            }

        return {"success": True, "url": url, "error": "", "output": pr_output[-4000:]}

    async def gh_cli(
        worktree_path: str,
        args: str,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Run a raw gh CLI command in the worktree (without the leading 'gh')."""
        parsed = shlex.split(args)
        if not parsed:
            return {"success": False, "returncode": -1, "stdout": "", "stderr": "No gh args provided"}
        return await rt.run_gh(worktree_path, parsed, timeout=timeout_seconds)

    # --- Request normalization ---

    async def summarize_request(request: str, context_json: str | None = None) -> dict[str, Any]:
        """Generate a stable work item ID and branch name for planning."""
        suffix = _derive_work_item_suffix(context_json)
        slug = "".join(c if c.isalnum() else "-" for c in request.lower())[:24].strip("-") or "builder"
        return {
            "work_item_id": f"{slug}-{suffix}",
            "branch_name": f"progenitor/{slug}-{suffix}",
            "request": request.strip() + (f"\n\nContext:\n{context_json}" if context_json else ""),
        }

    # --- Tool registration ---

    return [
        StructuredTool.from_function(
            coroutine=create_worktree, name="create_worktree",
            description="Create an isolated git worktree for the task.",
        ),
        StructuredTool.from_function(
            coroutine=cleanup_worktrees, name="cleanup_worktrees",
            description="Remove worktrees older than max_age_hours.",
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
        StructuredTool.from_function(
            coroutine=commit_changes, name="commit_changes",
            description="Stage and commit all changes in the worktree.",
        ),
        StructuredTool.from_function(
            coroutine=create_pull_request, name="create_pull_request",
            description="Push branch and create a GitHub pull request.",
        ),
        StructuredTool.from_function(
            coroutine=gh_cli, name="gh_cli",
            description="Run a gh CLI command in the worktree (pass args only, no leading gh).",
        ),
        StructuredTool.from_function(
            coroutine=summarize_request, name="summarize_request",
            description="Generate work item ID and branch name before planning.",
        ),
    ]
