"""Copilot ACP stdio integration."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    DeclineElicitationResponse,
    DeniedOutcome,
    ElicitationMode,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    ToolCallStart,
    ToolCallUpdate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ACP Client — collects streaming output from Copilot
# ---------------------------------------------------------------------------


class ACPClient(Client):
    """Collects streaming text and tool call updates from Copilot ACP."""

    def __init__(self, work_item_id: str = ""):
        self.work_item_id = work_item_id
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def reset(self, work_item_id: str) -> None:
        self.work_item_id = work_item_id
        self.text_parts.clear()
        self.tool_calls.clear()
        self.errors.clear()

    def on_connect(self, conn: Any) -> None:
        _ = conn

    async def session_update(self, session_id: str, update: Any, **_kw: Any) -> None:
        if isinstance(update, AgentMessageChunk):
            text = getattr(getattr(update, "content", None), "text", None)
            if text:
                self.text_parts.append(text)
        elif isinstance(update, ToolCallStart):
            self.tool_calls.append(
                {
                    "name": getattr(update, "title", "?"),
                    "id": getattr(update, "tool_call_id", "?"),
                }
            )

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        _ = (session_id, tool_call, kwargs)
        if options:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(
                    option_id=options[0].option_id,
                    outcome="selected",
                )
            )
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # Protocol-required stubs — not used, fail fast
    async def create_terminal(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: create_terminal")

    async def read_text_file(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: read_text_file")

    async def write_text_file(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: write_text_file")

    async def terminal_output(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: terminal_output")

    async def release_terminal(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: release_terminal")

    async def kill_terminal(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: kill_terminal")

    async def wait_for_terminal_exit(self, *a: Any, **kw: Any) -> Any:
        raise RuntimeError("Unsupported ACP callback: wait_for_terminal_exit")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass

    async def complete_elicitation(self, elicitation_id: str, **_kw: Any) -> None:
        _ = elicitation_id

    async def create_elicitation(
        self, message: str, mode: ElicitationMode, **_kw: Any
    ) -> DeclineElicitationResponse:
        _ = (message, mode)
        return DeclineElicitationResponse(action="decline")

    def get_output(self) -> str:
        return "".join(self.text_parts)


# ---------------------------------------------------------------------------
# ACP Manager — spawns and manages the Copilot stdio process
# ---------------------------------------------------------------------------


class ACPManager:
    """Manages a Copilot ACP stdio process with connection reuse."""

    def __init__(
        self,
        copilot_cmd: str = "copilot",
        model: str | None = None,
        flags: list[str] | None = None,
        allowed_dirs: list[str] | None = None,
        timeout: int = 1200,
    ):
        self.copilot_cmd = copilot_cmd
        self.model = model
        self.flags = flags or []
        self.allowed_dirs = allowed_dirs or []
        self.timeout = timeout
        self._client: ACPClient | None = None
        self._conn: Any = None
        self._spawn_cm: Any = None
        self._init_lock = asyncio.Lock()
        self._prompt_lock = asyncio.Lock()
        self._closed = False

    def _build_args(self) -> list[str]:
        args = ["--acp", "--yolo"]
        if self.model:
            args.extend(["--model", self.model])
        args.extend(self.flags)
        for d in self.allowed_dirs:
            if d:
                args.extend(["--add-dir", str(Path(d).resolve())])
        return args

    async def _ensure_ready(self, cwd: Path, work_item_id: str) -> None:
        if self._closed:
            raise RuntimeError("ACP manager is closed")
        if self._conn is not None:
            return
        async with self._init_lock:
            if self._conn is not None:
                return
            client = ACPClient(work_item_id)
            args = self._build_args()
            logger.info("ACP: spawning %s %s (cwd=%s)", self.copilot_cmd, " ".join(args), cwd)
            # Pass current environment to ensure subprocess inherits GitHub tokens
            self._spawn_cm = spawn_agent_process(
                client, self.copilot_cmd, *args, cwd=str(cwd), env=dict(os.environ)
            )
            conn, _ = await self._spawn_cm.__aenter__()
            await conn.initialize(protocol_version=PROTOCOL_VERSION)
            self._client = client
            self._conn = conn

    async def close(self) -> None:
        """Close ACP process and transport safely.

        On shutdown, Uvicorn cancellation can overlap with ACP stream handling.
        Guard close with locks and swallow benign double-close race errors.
        """
        if self._closed and self._spawn_cm is None:
            return

        async with self._init_lock:
            self._closed = True
            acquired_prompt_lock = False
            try:
                try:
                    await asyncio.wait_for(self._prompt_lock.acquire(), timeout=10)
                    acquired_prompt_lock = True
                except TimeoutError:
                    logger.warning("ACP shutdown timed out waiting for active prompt lock")

                if self._spawn_cm is not None:
                    try:
                        await self._spawn_cm.__aexit__(None, None, None)
                    except RuntimeError as exc:
                        if "asynchronous generator is already running" in str(exc):
                            logger.warning("ACP shutdown race ignored: %s", exc)
                        else:
                            raise
            finally:
                if acquired_prompt_lock:
                    self._prompt_lock.release()
                self._conn = None
                self._client = None
                self._spawn_cm = None

    async def prompt(self, text: str, cwd: Path, work_item_id: str = "") -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            await self._ensure_ready(cwd, work_item_id)
            assert self._client is not None and self._conn is not None

            async with self._prompt_lock:
                self._client.reset(work_item_id)
                session = await self._conn.new_session(cwd=str(cwd), mcp_servers=[])
                resp: PromptResponse = await asyncio.wait_for(
                    self._conn.prompt(prompt=[text_block(text)], session_id=session.session_id),
                    timeout=self.timeout,
                )
                stop = getattr(resp, "stop_reason", None)
                return {
                    "success": stop in ("end_turn", "stop", None) and not self._client.errors,
                    "stdout": self._client.get_output(),
                    "stderr": "\n".join(self._client.errors),
                    "stop_reason": str(stop) if stop else None,
                    "tool_calls": self._client.tool_calls,
                    "elapsed": time.perf_counter() - t0,
                }

        except TimeoutError:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Timeout ({self.timeout}s)",
                "stop_reason": "timeout",
                "tool_calls": [],
                "elapsed": time.perf_counter() - t0,
            }
        except Exception as exc:
            return {
                "success": False,
                "stdout": "",
                "stderr": str(exc),
                "stop_reason": "error",
                "tool_calls": [],
                "elapsed": time.perf_counter() - t0,
            }


# ---------------------------------------------------------------------------
# Log capture — writes ACP session output to files
# ---------------------------------------------------------------------------


class ACPLogCapture:
    """Writes ACP session output to log files."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir / "copilot_acp"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write_log(self, work_item_id: str, result: dict[str, Any]) -> Path:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        base = self.log_dir / f"{ts}_{work_item_id}"

        with open(base.with_suffix(".log"), "w", encoding="utf-8") as f:
            f.write(f"# Work Item: {work_item_id}\n")
            f.write(f"# Success: {result.get('success')}\n")
            f.write(f"# Elapsed: {result.get('elapsed', 0):.2f}s\n\n")
            f.write(result.get("stdout", ""))
            if result.get("stderr"):
                f.write(f"\n\n# Errors:\n{result['stderr']}")

        with open(base.with_suffix(".stdout.log"), "w", encoding="utf-8") as f:
            f.write(result.get("stdout", ""))

        with open(base.with_suffix(".stderr.log"), "w", encoding="utf-8") as f:
            f.write(result.get("stderr", ""))

        return base.with_suffix(".log")


# ---------------------------------------------------------------------------
# CopilotCLI — high-level build wrapper
# ---------------------------------------------------------------------------


class CopilotCLI:
    """High-level wrapper for invoking Copilot builds via ACP."""

    def __init__(
        self,
        copilot_cmd: str = "copilot",
        model: str | None = None,
        flags: list[str] | None = None,
        allowed_dirs: list[str] | None = None,
        timeout: int = 1200,
        log_dir: Path | None = None,
    ):
        self._manager = ACPManager(
            copilot_cmd=copilot_cmd,
            model=model,
            flags=flags,
            allowed_dirs=allowed_dirs or [],
            timeout=timeout,
        )
        self._log = ACPLogCapture(log_dir) if log_dir else None

    async def build(
        self,
        work_item_id: str,
        worktree_path: Path,
        prompt: str,
        target_path: str | None = None,
    ) -> dict[str, Any]:
        _ = target_path  # reserved for future scoping
        result = await self._manager.prompt(prompt, worktree_path, work_item_id)

        if self._log:
            self._log.write_log(work_item_id, result)

        diff = await self._git_diff(worktree_path)
        return {
            "success": result.get("success", False),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "summary": self._extract_summary(result.get("stdout", "")),
            "diff": diff,
            "tool_calls": result.get("tool_calls", []),
            "elapsed": result.get("elapsed", 0),
        }

    async def close(self) -> None:
        """Close underlying ACP resources."""
        await self._manager.close()

    @staticmethod
    def _extract_summary(output: str) -> str:
        lines: list[str] = []
        capture = False
        for line in output.splitlines():
            if "Total usage" in line or "Total duration" in line:
                capture = True
            if capture:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    async def _git_diff(cwd: Path) -> str:
        for args in (["diff", "--cached", "--stat"], ["diff", "--stat"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    *args,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0 and out:
                    return out.decode()
            except (TimeoutError, OSError):
                pass
        return ""
