"""Minimal toolset for the Builder Copilot DeepAgent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool

from app.agents.builder_agent.tools.copilot import CopilotCLI
from app.agents.builder_agent.tools.gh import gh_create_pr_from_worktree
from app.agents.builder_agent.tools.git import GitWorktreeManager

from .config import BuilderCopilotConfig

logger = logging.getLogger(__name__)


def _derive_work_item_suffix(context_json: str | None) -> str:
    """Prefer stable A2A identifiers when available."""
    if context_json:
        try:
            context = json.loads(context_json)
        except json.JSONDecodeError:
            context = None
        if isinstance(context, dict):
            for key in ("a2a_task_id", "task_id", "run_id", "a2a_context_id", "context_id"):
                value = context.get(key)
                if isinstance(value, str) and value.strip():
                    sanitized = "".join(ch for ch in value.lower() if ch.isalnum())
                    if sanitized:
                        return sanitized[:16]
    return "manual"


async def _configure_git_identity(workspace: Path) -> None:
    """Set local git identity from environment when provided."""
    git_user_name = (os.getenv("GIT_AUTHOR_NAME") or "").strip()
    git_user_email = (os.getenv("GIT_AUTHOR_EMAIL") or "").strip()
    if not git_user_name or not git_user_email:
        logger.info("Skipping git identity setup: GIT_AUTHOR_NAME or GIT_AUTHOR_EMAIL not set")
        return

    async def _run_git(*args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode
        assert rc is not None
        return int(rc), stdout.decode(errors="replace"), stderr.decode(errors="replace")

    for key, value in (("user.name", git_user_name), ("user.email", git_user_email)):
        rc, _out, err = await _run_git("-C", str(workspace), "config", key, value)
        if rc != 0:
            raise RuntimeError(f"Failed to set git {key}: {err[:500]}")


async def ensure_workspace() -> None:
    """Ensure the mounted workspace is a git repo before worktree creation."""
    workspace_env = os.getenv("PROGENITOR_PROJECT_ROOT")
    if not workspace_env:
        return

    workspace = Path(workspace_env)
    workspace.mkdir(parents=True, exist_ok=True)

    if (workspace / ".git").exists():
        await _configure_git_identity(workspace)
        return

    repo_url = os.getenv("PROGENITOR_REPO_URL")
    if not repo_url:
        logger.warning(
            "PROGENITOR_PROJECT_ROOT=%s is not a git repo and PROGENITOR_REPO_URL is not set",
            workspace,
        )
        return

    async def _run_git(*args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        rc = proc.returncode
        assert rc is not None
        return int(rc), stdout.decode(errors="replace"), stderr.decode(errors="replace")

    try:
        is_empty = not any(workspace.iterdir())
    except Exception:
        is_empty = False

    if is_empty:
        rc, _out, err = await _run_git("clone", repo_url, str(workspace))
        if rc != 0:
            raise RuntimeError(f"Failed to clone repo into workspace: {err[:500]}")
        await _configure_git_identity(workspace)
        return

    rc, _out, err = await _run_git("-C", str(workspace), "init")
    if rc != 0:
        raise RuntimeError(f"Failed to init git repo: {err[:500]}")

    rc, _out, _err = await _run_git("-C", str(workspace), "remote", "add", "origin", repo_url)
    if rc != 0:
        await _run_git("-C", str(workspace), "remote", "set-url", "origin", repo_url)

    rc, _out, err = await _run_git("-C", str(workspace), "fetch", "--prune", "origin")
    if rc != 0:
        raise RuntimeError(f"Failed to fetch origin: {err[:500]}")

    rc, _out, err = await _run_git("-C", str(workspace), "checkout", "-B", "main", "origin/main")
    if rc != 0:
        rc, _out, err = await _run_git("-C", str(workspace), "checkout", "-B", "main", "origin/master")
        if rc != 0:
            raise RuntimeError(f"Failed to checkout default branch: {err[:500]}")
    await _configure_git_identity(workspace)


class BuilderCopilotToolRuntime:
    """Thin wrapper over the existing builder-agent utilities."""

    def __init__(
        self,
        config: BuilderCopilotConfig,
        project_root: str | Path,
        log_dir: str | Path | None = None,
    ):
        repo_path = Path(os.getenv("PROGENITOR_PROJECT_ROOT") or project_root)
        self.repo_path = repo_path
        self.worktree_manager = GitWorktreeManager(
            worktrees_dir=config.storage.worktree_dir,
            repo_path=repo_path,
        )
        self.copilot = CopilotCLI(
            copilot_cmd=config.copilot.bin_path,
            model=config.copilot.model,
            default_flags=config.copilot.default_flags,
            allowed_dirs=config.copilot.acp_allowed_dirs,
            timeout=config.copilot.timeout_seconds,
            logs_dir=Path(log_dir) if log_dir else None,
        )

    async def run_command(
        self,
        worktree_path: str,
        command: str,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run a shell command inside the target worktree."""
        cwd = Path(worktree_path)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            proc.kill()
            return {
                "success": False,
                "returncode": -1,
                "stdout": "",
                "stderr": f"Timed out after {timeout_seconds}s",
            }
        stdout = (stdout_b or b"").decode(errors="replace")
        stderr = (stderr_b or b"").decode(errors="replace")
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": stdout[-12000:],
            "stderr": stderr[-12000:],
        }

    async def current_branch(self, worktree_path: str) -> str:
        """Return the current branch for the worktree."""
        result = await self.run_command(worktree_path, "git rev-parse --abbrev-ref HEAD", timeout_seconds=60)
        branch = str(result.get("stdout") or "").strip()
        if not branch:
            raise RuntimeError(f"Could not determine branch: {result.get('stderr', '')}")
        return branch


def create_builder_copilot_tools(
    *,
    config: BuilderCopilotConfig,
    project_root: str | Path,
    log_dir: str | Path | None = None,
) -> list[StructuredTool]:
    """Create the minimal custom tools for Builder Copilot."""
    runtime = BuilderCopilotToolRuntime(config=config, project_root=project_root, log_dir=log_dir)

    async def create_worktree_tool(
        work_item_id: str,
        branch_name: str | None = None,
    ) -> dict[str, str]:
        """Create a git worktree and return its path and branch."""
        path = await runtime.worktree_manager.create_worktree(work_item_id, branch_name=branch_name)
        branch = await runtime.current_branch(str(path))
        return {"worktree_path": str(path), "branch": branch}

    async def run_copilot_build_tool(
        work_item_id: str,
        worktree_path: str,
        prompt: str,
        target_path: str | None = None,
    ) -> dict[str, Any]:
        """Run GitHub Copilot against the worktree with the supplied prompt."""
        result = await runtime.copilot.build(
            work_item_id=work_item_id,
            worktree_path=Path(worktree_path),
            prompt=prompt,
            target_path=target_path,
        )
        return {
            "success": bool(result.get("success")),
            "summary": result.get("summary") or "",
            "stdout": str(result.get("stdout") or "")[-12000:],
            "stderr": str(result.get("stderr") or "")[-12000:],
            "diff": str(result.get("diff") or "")[-12000:],
        }

    async def build_progenitor_tool(
        work_item_id: str,
        worktree_path: str,
        prompt: str,
    ) -> dict[str, Any]:
        """Run GitHub Copilot in ACP mode scoped to app/progenitor."""
        return await run_copilot_build_tool(
            work_item_id=work_item_id,
            worktree_path=worktree_path,
            prompt=prompt,
            target_path="app/progenitor",
        )

    async def run_worktree_command_tool(
        worktree_path: str,
        command: str,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Run a command in a worktree for test/lint/build validation."""
        return await runtime.run_command(worktree_path, command, timeout_seconds=timeout_seconds)

    async def python_search_tool(
        worktree_path: str,
        query: str,
        glob_pattern: str = "*.py",
    ) -> dict[str, Any]:
        """Search Python files in the worktree using ripgrep."""
        command = f'rg -n --glob "{glob_pattern}" -- "{query}"'
        return await runtime.run_command(worktree_path, command, timeout_seconds=120)

    async def commit_changes_tool(
        worktree_path: str,
        message: str,
    ) -> dict[str, Any]:
        """Commit all current changes in the worktree."""
        commit_sha = await runtime.worktree_manager.commit_changes(Path(worktree_path), message)
        return {"committed": commit_sha is not None, "commit": commit_sha or ""}

    async def create_pull_request_tool(
        worktree_path: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> dict[str, Any]:
        """Push the worktree branch and create a PR via GitHub CLI."""
        branch = await runtime.current_branch(worktree_path)
        success, result = await gh_create_pr_from_worktree(
            worktree_path=Path(worktree_path),
            branch=branch,
            title=title,
            body=body,
            base=base,
        )
        return {"success": success, "url": result if success else "", "error": "" if success else result}

    async def summarize_request_tool(
        request: str,
        context_json: str | None = None,
    ) -> dict[str, Any]:
        """Create a stable work item id and normalized request text for a new build."""
        suffix = _derive_work_item_suffix(context_json)
        slug = "".join(ch if ch.isalnum() else "-" for ch in request.lower())[:24].strip("-") or "builder-copilot"
        branch_name = f"progenitor/{slug}-{suffix}"
        normalized = request.strip()
        if context_json:
            normalized = f"{normalized}\n\nContext:\n{context_json}"
        return {
            "work_item_id": f"{slug}-{suffix}",
            "branch_name": branch_name,
            "normalized_request": normalized,
        }

    return [
        StructuredTool.from_function(
            coroutine=create_worktree_tool,
            name="create_worktree",
            description="Create an isolated git worktree for the current task and return its path and branch.",
        ),
        StructuredTool.from_function(
            coroutine=run_copilot_build_tool,
            name="run_copilot_build",
            description="Run GitHub Copilot in ACP mode to implement changes inside a worktree.",
        ),
        StructuredTool.from_function(
            coroutine=build_progenitor_tool,
            name="build_progenitor",
            description="Run GitHub Copilot in ACP mode to implement progenitor changes under app/progenitor.",
        ),
        StructuredTool.from_function(
            coroutine=run_worktree_command_tool,
            name="run_worktree_command",
            description="Run a shell command inside the target worktree for build, test, or lint steps.",
        ),
        StructuredTool.from_function(
            coroutine=python_search_tool,
            name="python_search",
            description="Search Python files in the worktree using ripgrep to inspect symbols or references.",
        ),
        StructuredTool.from_function(
            coroutine=commit_changes_tool,
            name="commit_changes",
            description="Commit all current worktree changes with a supplied commit message.",
        ),
        StructuredTool.from_function(
            coroutine=create_pull_request_tool,
            name="create_pull_request",
            description="Push the current worktree branch and create a GitHub pull request.",
        ),
        StructuredTool.from_function(
            coroutine=summarize_request_tool,
            name="summarize_request",
            description="Generate a stable work item id and normalized request text before planning.",
        ),
    ]
