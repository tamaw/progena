"""
GitHub CLI (gh) wrapper for PR lifecycle management.

Provides async functions to push branches, create PRs, check PR status,
list PRs, and merge PRs using the `gh` CLI tool. These are called by the
publish node after verification passes.
"""

import asyncio
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class GhCliArgs(BaseModel):
    args: list[str] = Field(description="GitHub CLI arguments, excluding the leading 'gh'.")
    timeout: float = Field(default=60.0, description="Command timeout in seconds.")
    cwd: str | None = Field(
        default=None,
        description="Optional working directory for the gh command.",
    )


def _extract_pr_url(text: str) -> str | None:
    m = re.search(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+", text or "")
    return m.group(0) if m else None


async def _run_gh(
    args: list[str],
    cwd: Path | None = None,
    timeout: float = 60.0,
) -> dict[str, str | int | None]:
    """Run a gh CLI command with timeout.

    Args:
        args: Arguments to pass to `gh`.
        cwd: Working directory for the command.
        timeout: Timeout in seconds.

    Returns:
        Dict with keys: returncode, stdout, stderr.
    """
    cmd = ["gh", *args]
    logger.info("GH: Running %s in %s", " ".join(cmd), cwd or ".")
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise
        except TimeoutError:
            logger.error("GH: Command timed out after %ss", timeout)
            process.kill()
            await process.communicate()
            return {"returncode": -1, "stdout": "", "stderr": f"Timed out after {timeout}s"}

        result: dict[str, str | int | None] = {
            "returncode": process.returncode,
            "stdout": stdout.decode() if stdout else "",
            "stderr": stderr.decode() if stderr else "",
        }
        if process.returncode != 0:
            logger.warning(
                "GH: Command failed (%s): %s", process.returncode, str(result["stderr"])[:300]
            )
        return result
    except FileNotFoundError:
        msg = "gh CLI not found. Install from https://cli.github.com/"
        logger.error("GH: %s", msg)
        return {"returncode": -1, "stdout": "", "stderr": msg}
    except Exception as exc:
        logger.error("GH: Exception: %s", exc)
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}


# ---------------------------------------------------------------------------
# Git push helper (uses git directly, not gh)
# ---------------------------------------------------------------------------


async def gh_push_branch(branch: str, cwd: Path, remote: str = "origin") -> tuple[bool, str]:
    """Push a branch to the remote.

    Args:
        branch: Branch name to push.
        cwd: Repo / worktree directory.
        remote: Remote name (default: origin).

    Returns:
        (success, message) tuple.
    """
    process = await asyncio.create_subprocess_exec(
        "git",
        "push",
        "--set-upstream",
        remote,
        branch,
        cwd=cwd,
        env=os.environ.copy(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120.0)
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    except TimeoutError:
        process.kill()
        await process.communicate()
        return False, "push timed out"
    if process.returncode == 0:
        return True, f"Pushed {branch} to {remote}"
    return False, (stderr.decode() if stderr else "push failed")


# ---------------------------------------------------------------------------
# PR lifecycle
# ---------------------------------------------------------------------------


async def gh_pr_create(
    title: str,
    body: str,
    base: str = "main",
    head: str | None = None,
    cwd: Path | None = None,
    draft: bool = False,
) -> tuple[bool, str]:
    """Create a pull request using `gh pr create`.

    Args:
        title: PR title.
        body: PR body / description.
        base: Base branch (default: main).
        head: Head branch. If None, gh infers from current branch.
        cwd: Working directory (should be inside the repo).
        draft: Create as draft PR.

    Returns:
        (success, pr_url_or_error) tuple.
    """
    args = ["pr", "create", "--title", title, "--body", body, "--base", base]
    if head:
        args.extend(["--head", head])
    if draft:
        args.append("--draft")

    result = await _run_gh(args, cwd=cwd)
    if result["returncode"] == 0:
        pr_output = str(result["stdout"]).strip()
        pr_url = _extract_pr_url(pr_output)
        if not pr_url:
            logger.error("GH: PR create output missing PR URL: %s", pr_output[:500])
            return False, f"gh pr create succeeded but no PR URL found in output: {pr_output[:500]}"
        logger.info("GH: PR created: %s", pr_url)
        return True, pr_url
    return False, str(result["stderr"])


async def gh_pr_status(cwd: Path | None = None) -> dict:
    """Get PR status for the current branch (JSON output).

    Returns:
        Parsed JSON dict from `gh pr status --json ...`, or error dict.
    """
    result = await _run_gh(
        [
            "pr",
            "status",
            "--json",
            "number,title,state,url,statusCheckRollup",
        ],
        cwd=cwd,
    )
    if result["returncode"] == 0:
        try:
            return cast(dict, json.loads(str(result["stdout"])))
        except json.JSONDecodeError:
            return {"error": "Failed to parse JSON", "raw": result["stdout"]}
    return {"error": result["stderr"]}


async def gh_pr_list(
    state: str = "open",
    limit: int = 10,
    cwd: Path | None = None,
) -> list[dict]:
    """List PRs using `gh pr list`.

    Args:
        state: Filter by state (open, closed, merged, all).
        limit: Maximum number of PRs to return.
        cwd: Working directory.

    Returns:
        List of PR dicts, or empty list on error.
    """
    result = await _run_gh(
        [
            "pr",
            "list",
            "--state",
            state,
            "--limit",
            str(limit),
            "--json",
            "number,title,state,url,headRefName",
        ],
        cwd=cwd,
    )
    if result["returncode"] == 0:
        try:
            return cast(list[dict], json.loads(str(result["stdout"])))
        except json.JSONDecodeError:
            logger.warning("GH: Failed to parse pr list JSON")
            return []
    return []


async def gh_pr_merge(
    pr: str | int,
    method: str = "squash",
    cwd: Path | None = None,
    delete_branch: bool = True,
) -> tuple[bool, str]:
    """Merge a PR using `gh pr merge`.

    Args:
        pr: PR number or URL.
        method: Merge method (merge, squash, rebase).
        cwd: Working directory.
        delete_branch: Delete branch after merge.

    Returns:
        (success, message) tuple.
    """
    args = ["pr", "merge", str(pr), f"--{method}"]
    if delete_branch:
        args.append("--delete-branch")

    result = await _run_gh(args, cwd=cwd)
    if result["returncode"] == 0:
        return True, f"PR {pr} merged via {method}"
    return False, str(result["stderr"])


# ---------------------------------------------------------------------------
# High-level helper used by the publish node
# ---------------------------------------------------------------------------


async def gh_create_pr_from_worktree(
    worktree_path: Path,
    branch: str,
    title: str,
    body: str,
    base: str = "main",
    draft: bool = False,
) -> tuple[bool, str]:
    """Push branch from worktree and create a PR.

    This is the main entry point used by the publish node.

    Args:
        worktree_path: Path to the git worktree.
        branch: Branch name (should match the worktree branch).
        title: PR title.
        body: PR description body.
        base: Base branch to merge into.
        draft: Whether to create as draft.

    Returns:
        (success, pr_url_or_error) tuple.
    """
    # Push the branch
    push_ok, push_msg = await gh_push_branch(branch, cwd=worktree_path)
    if not push_ok:
        logger.error("GH: Push failed: %s", push_msg)
        return False, f"Push failed: {push_msg}"

    # Create the PR
    return await gh_pr_create(
        title=title,
        body=body,
        base=base,
        head=branch,
        cwd=worktree_path,
        draft=draft,
    )


class GhCliTool(BaseSystemTool):
    """Run gh CLI commands from container/project-root scope."""

    @property
    def name(self) -> str:
        return "gh_cli"

    @property
    def description(self) -> str:
        return "Run GitHub CLI commands (gh) at container scope, outside per-worktree execution."

    @property
    def keywords(self) -> list[str]:
        return ["gh", "gh cli", "github cli", "gh auth", "gh api"]

    @property
    def deepagent_args_schema(self) -> type[BaseModel] | None:
        return GhCliArgs

    def deepagent_prepare_args(self, **kwargs: object) -> tuple[str, dict[str, object] | None]:
        tool_args: dict[str, object] = {
            "args": kwargs.get("args") or [],
            "timeout": kwargs.get("timeout", 60.0),
        }
        if kwargs.get("cwd"):
            tool_args["cwd"] = kwargs.get("cwd")
        return "", tool_args

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        timeout_raw = args.get("timeout", 60)
        cwd_raw = args.get("cwd")
        cmd_args: list[str] = []

        if isinstance(args.get("args"), list):
            cmd_args = [str(item).strip() for item in args.get("args", []) if str(item).strip()]
        elif isinstance(args.get("args"), str):
            cmd_args = [part for part in shlex.split(str(args.get("args") or "").strip()) if part]
        else:
            request = str(context.user_input or "").strip()
            if request:
                parsed = shlex.split(request)
                if parsed and parsed[0] == "gh":
                    parsed = parsed[1:]
                cmd_args = parsed

        if not cmd_args:
            return ToolResult.fail(
                "Missing gh arguments. Pass tool_args={'args': ['auth','status']} or provide a gh command."
            )

        try:
            timeout = float(timeout_raw)
        except Exception:
            timeout = 60.0
        if timeout <= 0:
            timeout = 60.0

        default_cwd = Path(
            os.getenv("PROGENITOR_PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))
        )
        cwd = (
            Path(str(cwd_raw)).expanduser()
            if isinstance(cwd_raw, str) and cwd_raw.strip()
            else default_cwd
        )
        if not cwd.exists() or not cwd.is_dir():
            return ToolResult.fail(f"Invalid cwd for gh_cli: {cwd}")

        result = await _run_gh(cmd_args, cwd=cwd, timeout=timeout)
        return ToolResult.ok(
            {
                "command": ["gh", *cmd_args],
                "cwd": str(cwd),
                "returncode": result.get("returncode"),
                "stdout": result.get("stdout"),
                "stderr": result.get("stderr"),
                "ok": result.get("returncode") == 0,
            }
        )
