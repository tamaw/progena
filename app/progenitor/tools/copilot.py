"""
Copilot CLI Integration for Progenitor.

The builder actuator for all build operations:
- A2A agents
- Internal tools
- MCP servers

Includes telemetry for observability of build operations.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


# Lazy telemetry imports
def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_metrics, get_tracer, SpanKind
        return get_metrics(), get_tracer(), SpanKind
    except ImportError:
        return None, None, None


class CopilotCLI:
    """Wrapper for Copilot CLI invocations."""

    def __init__(
        self,
        config_path: Path,
        timeout: int = 300,
        log_callback=None,
    ):
        """Initialize Copilot CLI wrapper.

        Args:
            config_path: Path to copilot.json config file.
            timeout: Default timeout for copilot commands (from PROGENITOR_COPILOT_TIMEOUT_SECONDS).
            log_callback: Optional callback for logging invocations.
        """
        self.config_path = Path(config_path)
        self.default_timeout = timeout
        self.config = self._load_config()
        self.log_callback = log_callback

    def _load_config(self) -> dict[str, Any]:
        """Load Copilot CLI configuration."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                config = json.load(f)
                # Use instance timeout if not specified in file
                if "timeout" not in config:
                    config["timeout"] = self.default_timeout
                return config
        return {
            "command": "copilot",
            "default_flags": ["--yolo"],
            "timeout": self.default_timeout,
            "model": "claude-haiku-4.5",
        }

    async def build(
        self,
        work_item_id: str,
        worktree_path: Path,
        prompt: str,
        target_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a build task via Copilot CLI."""
        worktree_path = Path(worktree_path)

        # Telemetry setup
        metrics, tracer, SpanKind = _get_telemetry()
        span = None
        start_time = time.perf_counter()

        if tracer and SpanKind:
            span = tracer.start_span(
                "tool.copilot.build",
                kind=SpanKind.CLIENT,
                attributes={
                    "copilot.work_item_id": work_item_id,
                    "copilot.worktree": str(worktree_path),
                    "copilot.target_path": target_path,
                    "copilot.prompt_length": len(prompt),
                    "copilot.model": self.config.get("model", "unknown"),
                },
            )

        # Resolve command
        cmd = self._build_command(prompt, target_path)
        logger.info("COPILOT: Building command for work item %s", work_item_id)
        logger.info("COPILOT: cmd=%s... in %s", cmd[:3], worktree_path)

        # Log the invocation
        invocation_log = {
            "work_item_id": work_item_id,
            "worktree_path": str(worktree_path),
            "command": cmd,
            "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
            "started_at": utcnow_isoformat(),
        }

        try:
            # Run Copilot CLI
            logger.info("COPILOT: Running command...")
            if span:
                span.add_event("copilot.command_start")

            result = await self._run_command(cmd, cwd=worktree_path)
            logger.info("COPILOT: Command completed with returncode=%s", result['returncode'])

            if span:
                span.add_event("copilot.command_complete", {"returncode": result["returncode"]})

            # Get git diff
            diff_result = await self._get_diff(worktree_path)

            # Record telemetry
            duration = time.perf_counter() - start_time
            success = result["returncode"] == 0

            if metrics:
                metrics.tool_calls_total.inc()
                metrics.tool_call_duration.observe(duration)
                if not success:
                    metrics.tool_call_errors.inc()

            if span:
                span.set_attribute("copilot.success", success)
                span.set_attribute("copilot.returncode", result["returncode"])
                span.set_attribute("copilot.duration_ms", duration * 1000)
                span.set_attribute("copilot.has_diff", bool(diff_result))
                if diff_result:
                    span.set_attribute("copilot.diff_lines", len(diff_result.splitlines()))

            invocation_log.update({
                "completed_at": utcnow_isoformat(),
                "success": result["returncode"] == 0,
                "stdout_preview": result["stdout"][:1000] if result["stdout"] else None,
                "stderr_preview": result["stderr"][:500] if result["stderr"] else None,
                "diff_preview": diff_result[:2000] if diff_result else None,
            })

            if self.log_callback:
                await self.log_callback(invocation_log)

            # End telemetry span on success
            if span and tracer:
                tracer.end_span(span)

            return {
                "success": result["returncode"] == 0,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "diff": diff_result,
                "returncode": result["returncode"],
            }

        except Exception as e:
            invocation_log.update({
                "completed_at": utcnow_isoformat(),
                "success": False,
                "error": str(e),
            })

            if self.log_callback:
                await self.log_callback(invocation_log)

            # Record error in telemetry
            if span:
                span.record_exception(e)
            if span and tracer:
                tracer.end_span(span)

            return {
                "success": False,
                "error": str(e),
            }

    def _build_command(self, prompt: str, target_path: Optional[str] = None) -> list[str]:
        """Build the Copilot CLI command."""
        cmd = [self.config.get("command", "copilot")]
        cmd.extend(self.config.get("default_flags", []))

        # Add model selection
        if "model" in self.config:
            cmd.extend(["--model", self.config["model"]])

        if target_path:
            cmd.extend(["--target", target_path])

        # Add prompt as argument
        cmd.extend(["-p", prompt])

        return cmd

    async def _run_command(
        self,
        cmd: list[str],
        cwd: Path,
    ) -> dict[str, Any]:
        """Run a command asynchronously."""
        timeout = self.config.get("timeout", 300)
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "COPILOT_NON_INTERACTIVE": "1"},
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return {
                "returncode": process.returncode,
                "stdout": stdout.decode() if stdout else "",
                "stderr": stderr.decode() if stderr else "",
            }

        except asyncio.TimeoutError:
            if process is not None:
                process.kill()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout} seconds",
            }

    async def _get_diff(self, worktree_path: Path) -> str:
        """Get git diff for the worktree."""
        try:
            result = await self._run_command(
                ["git", "diff", "--cached", "--stat"],
                cwd=worktree_path
            )

            if result["returncode"] == 0:
                return result["stdout"]

            # Try unstaged diff
            result = await self._run_command(
                ["git", "diff", "--stat"],
                cwd=worktree_path
            )
            return result["stdout"]

        except Exception:
            return ""

    async def validate_installation(self) -> bool:
        """Check if Copilot CLI is installed and accessible."""
        try:
            result = await self._run_command(
                [self.config.get("command", "copilot"), "--version"],
                cwd=Path.cwd()
            )
            return result["returncode"] == 0
        except Exception:
            return False
