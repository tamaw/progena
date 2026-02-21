"""
Copilot CLI Integration for Progenitor via ACP (Agent Client Protocol).

The builder actuator for all build operations:
- A2A agents
- Internal tools
- MCP servers

Uses Copilot's ACP mode for communication instead of subprocess stdin/stdout.
Includes logging for observability of build operations.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..config import CopilotConfig
from ..runtime.copilot_acp import ACPLogCapture, CopilotACPManager
from ..runtime.time_utils import utcnow_isoformat
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class CopilotCLI:
    """Wrapper for Copilot invocations via ACP protocol.

    Uses the Agent Client Protocol to communicate with a running
    Copilot ACP server instead of spawning new subprocess for each build.
    """

    def __init__(
        self,
        copilot_cmd: str = "copilot",
        model: str | None = None,
        default_flags: list[str] | None = None,
        allowed_dirs: list[str] | None = None,
        acp_manager: CopilotACPManager | None = None,
        timeout: int = 1200,  # 20 minutes per request
        log_callback=None,
        logs_dir: Path | None = None,
    ):
        """Initialize Copilot CLI wrapper.

        Args:
            copilot_cmd: Copilot CLI command or path.
            model: Copilot model override (optional).
            default_flags: Default Copilot CLI flags.
            allowed_dirs: ACP allowed directories.
            acp_manager: CopilotACPManager instance for ACP communication.
            timeout: Default timeout for copilot commands (20 minutes).
            log_callback: Optional callback for logging invocations.
            logs_dir: Directory for log files (for ACPLogCapture).
        """
        self.copilot_cmd = copilot_cmd
        self.model = model
        self.default_flags = default_flags or ["--yolo"]
        self.allowed_dirs = allowed_dirs or []
        self.default_timeout = timeout
        self.log_callback = log_callback

        if acp_manager is not None:
            self.acp_manager = acp_manager
        else:
            # Create a default manager from config
            self.acp_manager = CopilotACPManager(
                copilot_cmd=self.copilot_cmd,
                model=self.model,
                yolo="--yolo" in self.default_flags,
                allowed_dirs=self.allowed_dirs,
                timeout=timeout,
            )

        # Setup log capture
        self.log_capture: ACPLogCapture | None = None
        if logs_dir:
            self.log_capture = ACPLogCapture(logs_dir)

        logger.info(
            "COPILOT: Initialized with cmd=%s, model=%s, timeout=%ds",
            self.copilot_cmd,
            self.model or "default",
            timeout,
        )

    async def build(
        self,
        work_item_id: str,
        worktree_path: Path,
        prompt: str,
        target_path: str | None = None,
        allowed_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Execute a build task via Copilot ACP."""
        worktree_path = Path(worktree_path)

        start_time = time.perf_counter()

        logger.info(
            "COPILOT: ===== Starting ACP build for work item %s =====",
            work_item_id,
        )
        logger.info("COPILOT: Prompt length: %d chars", len(prompt))
        logger.info("COPILOT: Working directory: %s", worktree_path)
        logger.info("COPILOT: Target path: %s", target_path)
        logger.info(
            "COPILOT: Prompt preview: %s",
            prompt[:300] + "..." if len(prompt) > 300 else prompt,
        )

        invocation_log: dict[str, Any] = {
            "work_item_id": work_item_id,
            "worktree_path": str(worktree_path),
            "mode": "acp",
            "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
            "started_at": utcnow_isoformat(),
        }

        try:
            logger.info("COPILOT: Calling ACP manager.prompt()")
            result = await self.acp_manager.prompt(
                prompt_text=prompt,
                cwd=worktree_path,
                work_item_id=work_item_id,
                allowed_dirs=allowed_dirs if allowed_dirs is not None else self.allowed_dirs,
            )

            success = result.get("success", False)
            logger.info(
                "COPILOT: ===== ACP build completed: success=%s, elapsed=%.2fs =====",
                success,
                result.get("elapsed_seconds", 0),
            )
            logger.info(
                "COPILOT: Output length: %d chars, tool calls: %d, chunks: %d",
                len(result.get("stdout", "")),
                len(result.get("tool_calls", [])),
                len(result.get("chunks", [])),
            )

            if self.log_capture:
                log_path = self.log_capture.write_log(work_item_id, result)
                logger.info("COPILOT: Session log written to %s", log_path)

            diff_result = await self._get_diff(worktree_path)
            if diff_result:
                logger.info(
                    "COPILOT: Git diff result (%d chars):\n%s",
                    len(diff_result),
                    diff_result[:500] + "..." if len(diff_result) > 500 else diff_result,
                )
            else:
                logger.info("COPILOT: No git diff detected")

            duration = time.perf_counter() - start_time

            invocation_log.update({
                "completed_at": utcnow_isoformat(),
                "duration_seconds": duration,
                "success": success,
                "stdout_preview": result.get("stdout", "")[:1000] if result.get("stdout") else None,
                "stderr_preview": result.get("stderr", "")[:500] if result.get("stderr") else None,
                "diff_preview": diff_result[:2000] if diff_result else None,
            })

            if self.log_callback:
                await self.log_callback(invocation_log)

            return {
                "success": success,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "summary": self._extract_summary_from_output(result.get("stdout", "")),
                "diff": diff_result,
                "returncode": 0 if success else 1,
                "tool_calls": result.get("tool_calls", []),
                "chunks": len(result.get("chunks", [])),
                "elapsed_seconds": result.get("elapsed_seconds", 0),
            }

        except Exception as e:
            logger.exception(
                "COPILOT: ===== ACP build FAILED: %s =====",
                e,
            )

            invocation_log.update({
                "completed_at": utcnow_isoformat(),
                "success": False,
                "error": str(e),
            })

            if self.log_callback:
                await self.log_callback(invocation_log)

            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": str(e),
                "returncode": 1,
            }

    def _extract_summary_from_output(self, output: str) -> str:
        """Extract the usage summary section from copilot output."""
        summary_lines = []
        in_summary = False

        for line in output.splitlines():
            if "Total usage" in line or "Total duration" in line:
                in_summary = True
            if in_summary:
                summary_lines.append(line)

        return "\n".join(summary_lines) if summary_lines else ""

    async def _get_diff(self, worktree_path: Path) -> str:
        """Get git diff for the worktree."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--cached",
                "--stat",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30.0)

            if process.returncode == 0 and stdout:
                return stdout.decode()

            process = await asyncio.create_subprocess_exec(
                "git",
                "diff",
                "--stat",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30.0)
            return stdout.decode() if stdout else ""

        except (TimeoutError, OSError) as e:
            logger.debug("Failed to get worktree diff for %s: %s", worktree_path, e)
            return ""

    async def validate_installation(self) -> bool:
        """Check if Copilot CLI is installed and accessible."""
        try:
            process = await asyncio.create_subprocess_exec(
                self.copilot_cmd,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=10.0)
            return process.returncode == 0
        except (TimeoutError, OSError, FileNotFoundError) as e:
            logger.debug("Copilot CLI validation failed: %s", e)
            return False


class CopilotTool(BaseSystemTool):
    """Expose Copilot builder configuration/status.

    Note: the actual build pipeline uses Copilot internally (builder actuator).
    This tool is intentionally status-oriented and does not directly execute
    builds.
    """

    @property
    def name(self) -> str:
        return "copilot"

    @property
    def description(self) -> str:
        return "Show Copilot (ACP) configuration used for build operations"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        cfg = CopilotConfig.from_env()
        return ToolResult.ok({
            "bin_path": cfg.bin_path,
            "model": cfg.model,
            "timeout_seconds": cfg.timeout_seconds,
            "default_flags": cfg.default_flags,
            "acp_port": cfg.acp_port,
            "acp_allowed_dirs": cfg.acp_allowed_dirs,
            "env": {
                "PROGENITOR_COPILOT_BIN": os.getenv("PROGENITOR_COPILOT_BIN"),
                "PROGENITOR_COPILOT_MODEL": os.getenv("PROGENITOR_COPILOT_MODEL"),
            },
            "note": "Copilot is used internally by the build actuator; this tool reports configuration.",
        })
