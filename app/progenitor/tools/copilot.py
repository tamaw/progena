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
import time
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
        logger.info("COPILOT: cmd args (excl prompt): %s in %s", cmd[:-1], worktree_path)

        # Log the invocation
        invocation_log = {
            "work_item_id": work_item_id,
            "worktree_path": str(worktree_path),
            "command": cmd[:-1],  # Don't log full prompt in command
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
                "summary": result.get("summary", ""),
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
                "summary": result.get("summary", ""),
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
        """Build the Copilot CLI command.

        Returns:
            Command args list. The prompt is passed directly via -p.
            For long/complex prompts, consider using a temp file.
        """
        cmd = [self.config.get("command", "copilot")]
        cmd.extend(self.config.get("default_flags", []))

        # Add model selection
        if "model" in self.config:
            cmd.extend(["--model", self.config["model"]])

        # Add allowed directories (restricts file access to only these dirs)
        for allowed_dir in self.config.get("allowed_dirs", []):
            cmd.extend(["--add-dir", allowed_dir])

        if target_path:
            cmd.extend(["--target", target_path])

        # Pass prompt directly - subprocess.exec handles quoting
        cmd.extend(["-p", prompt])

        return cmd

    async def _run_command(
        self,
        cmd: list[str],
        cwd: Path,
        stream_to_log: bool = True,
    ) -> dict[str, Any]:
        """Run a command asynchronously with streaming output.

        Args:
            cmd: Command and arguments to run.
            cwd: Working directory.
            stream_to_log: If True, log output lines as they arrive.

        Returns:
            Dict with returncode, stdout, stderr, and summary.
        """
        timeout = self.config.get("timeout", 300)
        process = None
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            # Log the command (truncate prompt for readability)
            # cmd ends with ["-p", prompt], so cmd[:-1] shows everything up to the prompt
            cmd_display = cmd[:-1] if len(cmd) > 1 else cmd
            logger.info("COPILOT: Executing: %s <prompt omitted>", " ".join(cmd_display))

            # Note: stdin=DEVNULL prevents Node.js stdin issues in nested subprocess chains
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def read_stream(stream, lines: list, prefix: str):
                """Read stream line by line and log."""
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode().rstrip()
                    lines.append(decoded)
                    if stream_to_log and decoded:
                        logger.info("COPILOT [%s]: %s", prefix, decoded)

            # Read stdout and stderr concurrently with timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, stdout_lines, "out"),
                        read_stream(process.stderr, stderr_lines, "err"),
                    ),
                    timeout=timeout
                )
                await process.wait()
            except asyncio.TimeoutError:
                logger.warning("COPILOT: Command timed out after %ds", timeout)
                await self._terminate_process_gracefully(process)
                return {
                    "returncode": -1,
                    "stdout": "\n".join(stdout_lines),
                    "stderr": "\n".join(stderr_lines) + f"\n[Timed out after {timeout}s]",
                    "summary": self._extract_summary(stdout_lines),
                }

            stdout_text = "\n".join(stdout_lines)
            stderr_text = "\n".join(stderr_lines)

            # Extract the summary section from output
            summary = self._extract_summary(stdout_lines)
            if summary:
                logger.info("COPILOT SUMMARY:\n%s", summary)

            return {
                "returncode": process.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "summary": summary,
            }

        except FileNotFoundError as e:
            logger.error("COPILOT: Command not found: %s", cmd[0])
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command not found: {cmd[0]} - {e}",
                "summary": "",
            }
        except Exception as e:
            logger.error("COPILOT: Unexpected error: %s", e)
            if process is not None:
                await self._terminate_process_gracefully(process)
            return {
                "returncode": -1,
                "stdout": "\n".join(stdout_lines),
                "stderr": "\n".join(stderr_lines) + f"\n[Error: {e}]",
                "summary": "",
            }

    def _extract_summary(self, lines: list[str]) -> str:
        """Extract the usage summary section from copilot output.

        The summary typically starts with 'Total usage' and includes
        duration and code change statistics.
        """
        summary_lines = []
        in_summary = False

        for line in lines:
            # Summary starts with "Total usage" or blank line before it
            if "Total usage" in line or "Total duration" in line:
                in_summary = True
            if in_summary:
                summary_lines.append(line)

        return "\n".join(summary_lines) if summary_lines else ""

    async def _terminate_process_gracefully(
        self,
        process: asyncio.subprocess.Process,
        grace_period: float = 5.0
    ) -> None:
        """Gracefully terminate a process with SIGTERM, then SIGKILL if needed.

        Args:
            process: The process to terminate.
            grace_period: Seconds to wait for graceful shutdown before force kill.
        """
        try:
            # First try SIGTERM (graceful shutdown)
            process.terminate()
            logger.debug("Sent SIGTERM to process %s", process.pid)

            try:
                # Wait for graceful shutdown
                await asyncio.wait_for(process.wait(), timeout=grace_period)
                logger.debug("Process %s terminated gracefully", process.pid)
            except asyncio.TimeoutError:
                # Process didn't terminate gracefully, force kill
                logger.warning(
                    "Process %s did not respond to SIGTERM after %.1fs, sending SIGKILL",
                    process.pid,
                    grace_period
                )
                process.kill()
                await process.wait()  # Clean up zombie process
                logger.debug("Process %s killed", process.pid)
        except ProcessLookupError:
            # Process already terminated
            logger.debug("Process already terminated")
        except Exception as e:
            logger.warning("Error terminating process: %s", e)

    async def _get_diff(self, worktree_path: Path) -> str:
        """Get git diff for the worktree."""
        try:
            result = await self._run_command(
                ["git", "diff", "--cached", "--stat"],
                cwd=worktree_path,
                stream_to_log=False,  # Don't log git output
            )

            if result["returncode"] == 0:
                return result["stdout"]

            # Try unstaged diff
            result = await self._run_command(
                ["git", "diff", "--stat"],
                cwd=worktree_path,
                stream_to_log=False,
            )
            return result["stdout"]

        except (OSError, asyncio.TimeoutError) as e:
            logger.debug("Failed to get worktree diff for %s: %s", worktree_path, e)
            return ""

    async def validate_installation(self) -> bool:
        """Check if Copilot CLI is installed and accessible."""
        try:
            result = await self._run_command(
                [self.config.get("command", "copilot"), "--version"],
                cwd=Path.cwd(),
                stream_to_log=False,  # Don't log version check
            )
            return result["returncode"] == 0
        except (OSError, FileNotFoundError, asyncio.TimeoutError) as e:
            logger.debug("Copilot CLI validation failed: %s", e)
            return False
