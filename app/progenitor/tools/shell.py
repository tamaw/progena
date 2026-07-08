"""
Shell access tools.

WARNING: These tools enable arbitrary command execution in the runtime
environment (typically a Docker container). Use with care.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_OUTPUT_CHARS = 10_000


def _detect_docker() -> bool:
    # Fast path commonly present in containers.
    try:
        if Path("/.dockerenv").exists():
            return True
    except Exception:
        return False

    # Fallback: cgroup hints (Linux-only).
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text(encoding="utf-8", errors="replace")
            return any(k in text for k in ("docker", "kubepods", "containerd"))
    except Exception:
        return False
    return False


def _resolve_shell() -> str | None:
    return shutil.which("bash") or shutil.which("sh")


def _coerce_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except Exception:
        timeout = _DEFAULT_TIMEOUT_SECONDS
    if timeout <= 0:
        timeout = _DEFAULT_TIMEOUT_SECONDS
    return min(timeout, _MAX_TIMEOUT_SECONDS)


_FENCE_RE = re.compile(r"```(?:bash|sh)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class ShellExecArgs(BaseModel):
    command: str = Field(description="Non-interactive shell command to execute.")
    timeout_seconds: float = Field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        description="Command timeout in seconds.",
    )
    cwd: str | None = Field(
        default=None,
        description="Optional working directory for the command.",
    )


def _extract_command_from_request(request: str) -> str:
    text = (request or "").strip()
    if not text:
        return ""

    # Prefer explicit code blocks.
    m = _FENCE_RE.search(text)
    if m:
        return (m.group(1) or "").strip()

    # Common prefixes.
    for prefix in ("command:", "cmd:"):
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip()

    # Drop explicit tool/shell invocation prefixes (optionally followed by separators).
    for prefix in (
        "shell_exec",
        "shell exec",
        "shell_check",
        "shell check",
        "shell",
        "bash",
        "sh",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].lstrip()
            text = text.lstrip(":>-").lstrip()
            break

    # If the user said "run <cmd>" / "execute <cmd>", drop the verb.
    for prefix in ("run ", "execute ", "shell "):
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip()

    return text


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"


class ShellCheckTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "shell_check"

    @property
    def description(self) -> str:
        return "Check whether a POSIX shell is available and whether we're running in Docker"

    async def execute(self, context: ToolContext) -> ToolResult:
        shell = _resolve_shell()
        return ToolResult.ok(
            {
                "platform": os.name,
                "in_docker": _detect_docker(),
                "shell": shell,
            }
        )


class ShellExecTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return (
            "Execute a non-interactive shell command inside the container (uses bash if available)"
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return ShellExecArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        if os.name == "nt":
            return ToolResult.fail("shell_exec is only supported on POSIX (Linux) containers.")

        args = context.tool_args or {}
        timeout = _coerce_timeout(
            args.get("timeout_seconds")
            or os.getenv("PROGENITOR_SHELL_TIMEOUT_SECONDS")
            or _DEFAULT_TIMEOUT_SECONDS
        )

        cwd: Path | None = None
        cwd_arg = args.get("cwd")
        if cwd_arg:
            try:
                candidate = Path(str(cwd_arg)).expanduser().resolve()
            except Exception as e:
                return ToolResult.fail(f"Invalid cwd: {cwd_arg} ({e})")
            if not candidate.exists() or not candidate.is_dir():
                return ToolResult.fail(f"cwd does not exist or is not a directory: {candidate}")
            cwd = candidate

        command = str(args.get("command") or "").strip()
        if not command:
            # Legacy callers (and tests) may still pass the command via user_input.
            command = _extract_command_from_request(str(context.user_input or ""))
        if not command:
            return ToolResult.fail("No shell command provided.")

        shell = _resolve_shell()
        if not shell:
            return ToolResult.fail("No shell found (expected bash or sh).")

        logger.info("SHELL: %s -lc %s (cwd=%s)", shell, command, cwd or os.getcwd())
        process = await asyncio.create_subprocess_exec(
            shell,
            "-lc",
            command,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout_b, stderr_b = await process.communicate()

        stdout = (stdout_b or b"").decode(errors="replace")
        stderr = (stderr_b or b"").decode(errors="replace")

        return ToolResult.ok(
            {
                "enabled": True,
                "in_docker": _detect_docker(),
                "shell": shell,
                "cwd": str(cwd) if cwd else os.getcwd(),
                "command": command,
                "timeout_seconds": timeout,
                "timed_out": timed_out,
                "returncode": process.returncode,
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
            }
        )
