"""
Shell access tools.

These tools are intentionally gated behind an environment variable because they
enable arbitrary command execution inside the running container.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_OUTPUT_CHARS = 10_000


def _shell_tools_enabled() -> bool:
    return os.getenv("PROGENITOR_ENABLE_SHELL_TOOLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _detect_docker() -> bool:
    # Fast path commonly present in containers.
    try:
        if Path("/.dockerenv").exists():
            return True
    except Exception:
        pass

    # Fallback: cgroup hints (Linux-only).
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text(encoding="utf-8", errors="replace")
            return any(k in text for k in ("docker", "kubepods", "containerd"))
    except Exception:
        pass
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


def _extract_command_from_request(request: str) -> str:
    text = (request or "").strip()
    if not text:
        return ""

    # Prefer explicit code blocks.
    m = _FENCE_RE.search(text)
    if m:
        return (m.group(1) or "").strip()

    # Common prefixes.
    for prefix in ("command:", "cmd:", "bash:", "sh:"):
        if text.lower().startswith(prefix):
            return text[len(prefix) :].strip()

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
        return "Check whether shell command tools are enabled and which shell is available"

    async def execute(self, _context: ToolContext) -> ToolResult:
        shell = _resolve_shell()
        return ToolResult.ok(
            {
                "enabled": _shell_tools_enabled(),
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
            "Execute a non-interactive shell command inside the container "
            "(requires PROGENITOR_ENABLE_SHELL_TOOLS=true; uses bash if available)"
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if not _shell_tools_enabled():
            return ToolResult.fail(
                "Shell tools are disabled. Set PROGENITOR_ENABLE_SHELL_TOOLS=true to enable."
            )

        if os.name == "nt":
            return ToolResult.fail("shell_exec is only supported on POSIX (Linux) containers.")

        tool_args = context.tool_args or {}
        request = str(tool_args.get("command") or tool_args.get("request") or context.user_input or "")
        command = _extract_command_from_request(request)
        if not command:
            return ToolResult.fail("No shell command provided.")

        shell = _resolve_shell()
        if not shell:
            return ToolResult.fail("No shell found (expected bash or sh).")

        timeout = _coerce_timeout(tool_args.get("timeout_seconds"))
        cwd_raw = tool_args.get("cwd")
        cwd = None
        if cwd_raw:
            try:
                cwd = Path(str(cwd_raw)).expanduser().resolve()
                if not cwd.exists() or not cwd.is_dir():
                    return ToolResult.fail(f"cwd does not exist or is not a directory: {cwd}")
            except Exception as e:
                return ToolResult.fail(f"Invalid cwd: {cwd_raw} ({e})")

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

