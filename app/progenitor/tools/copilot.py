"""
Copilot CLI Integration for Progenitor via ACP (Agent Client Protocol).

The builder actuator for all build operations:
- A2A agents
- Internal tools
- MCP servers

Uses Copilot's ACP mode for communication instead of subprocess stdin/stdout.
Includes telemetry for observability of build operations.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from ..runtime.time_utils import utcnow_isoformat
from ..runtime.copilot_acp import CopilotACPManager, ACPLogCapture

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
    """Wrapper for Copilot invocations via ACP protocol.

    Uses the Agent Client Protocol to communicate with a running
    Copilot ACP server instead of spawning new subprocess for each build.
    """

    def __init__(
        self,
        config_path: Path,
        acp_manager: Optional[CopilotACPManager] = None,
        timeout: int = 1200,  # 20 minutes per request
        log_callback=None,
        logs_dir: Optional[Path] = None,
    ):
        """Initialize Copilot CLI wrapper.

        Args:
            config_path: Path to copilot.json config file.
            acp_manager: CopilotACPManager instance for ACP communication.
            timeout: Default timeout for copilot commands (20 minutes).
            log_callback: Optional callback for logging invocations.
            logs_dir: Directory for log files (for ACPLogCapture).
        """
        self.config_path = Path(config_path)
        self.default_timeout = timeout
        self.config = self._load_config()
        self.log_callback = log_callback

        if acp_manager is not None:
            self.acp_manager = acp_manager
        else:
            # Create a default manager from config
            self.acp_manager = CopilotACPManager(
                copilot_cmd=self.config.get("command", "copilot"),
                model=self.config.get("model"),
                yolo="--yolo" in self.config.get("default_flags", []),
                allowed_dirs=self.config.get("allowed_dirs", []),
                timeout=timeout,
            )

        # Setup log capture
        self.log_capture: Optional[ACPLogCapture] = None
        if logs_dir:
            self.log_capture = ACPLogCapture(logs_dir)

        logger.info(
            "COPILOT: Initialized with config=%s, timeout=%ds",
            config_path,
            timeout,
        )

    def _load_config(self) -> dict[str, Any]:
        """Load Copilot CLI configuration."""
        if self.config_path.exists():
            with open(self.config_path, 'r') as f:
                config = json.load(f)
                # Use instance timeout if not specified in file
                if "timeout" not in config:
                    config["timeout"] = self.default_timeout
                logger.info(
                    "COPILOT: Loaded config from %s: model=%s, timeout=%ds",
                    self.config_path,
                    config.get("model", "default"),
                    config.get("timeout", self.default_timeout),
                )
                return config
        logger.warning(
            "COPILOT: Config file not found at %s, using defaults",
            self.config_path,
        )
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
        """Execute a build task via Copilot ACP.

        Args:
            work_item_id: Unique identifier for the work item.
            worktree_path: Working directory for the build.
            prompt: The prompt text that instructs Copilot.
            target_path: Optional target path for the build.

        Returns:
            Dict with success status, output, and any errors.
        """
        worktree_path = Path(worktree_path)

        # Telemetry setup
        metrics, tracer, SpanKind = _get_telemetry()
        span = None
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
                    "copilot.mode": "acp",
                },
            )

        # Log the invocation
        invocation_log: dict[str, Any] = {
            "work_item_id": work_item_id,
            "worktree_path": str(worktree_path),
            "mode": "acp",
            "prompt_preview": prompt[:500] + "..." if len(prompt) > 500 else prompt,
            "started_at": utcnow_isoformat(),
        }

        try:
            if span:
                span.add_event("copilot.acp_start")

            # Execute via ACP manager
            logger.info("COPILOT: Calling ACP manager.prompt()")
            result = await self.acp_manager.prompt(
                prompt_text=prompt,
                cwd=worktree_path,
                work_item_id=work_item_id,
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

            if span:
                span.add_event("copilot.acp_complete", {
                    "success": success
                })

            # Write to log file if log capture is configured
            if self.log_capture:
                log_path = self.log_capture.write_log(work_item_id, result)
                logger.info("COPILOT: Session log written to %s", log_path)

            # Get git diff
            diff_result = await self._get_diff(worktree_path)
            if diff_result:
                logger.info(
                    "COPILOT: Git diff result (%d chars):\n%s",
                    len(diff_result),
                    diff_result[:500] + "..." if len(diff_result) > 500 else diff_result,
                )
            else:
                logger.info("COPILOT: No git diff detected")

            # Record telemetry
            duration = time.perf_counter() - start_time

            if metrics:
                metrics.tool_calls_total.inc()
                metrics.tool_call_duration.observe(duration)
                if not success:
                    metrics.tool_call_errors.inc()

            if span:
                span.set_attribute("copilot.success", success)
                span.set_attribute("copilot.duration_ms", duration * 1000)
                span.set_attribute("copilot.has_diff", bool(diff_result))
                if diff_result:
                    span.set_attribute("copilot.diff_lines", len(diff_result.splitlines()))

            invocation_log.update({
                "completed_at": utcnow_isoformat(),
                "success": success,
                "stdout_preview": result.get("stdout", "")[:1000] if result.get("stdout") else None,
                "stderr_preview": result.get("stderr", "")[:500] if result.get("stderr") else None,
                "diff_preview": diff_result[:2000] if diff_result else None,
            })

            if self.log_callback:
                await self.log_callback(invocation_log)

            # End telemetry span on success
            if span and tracer:
                tracer.end_span(span)

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

            # Record error in telemetry
            if span:
                span.record_exception(e)
            if span and tracer:
                tracer.end_span(span)

            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": str(e),
                "returncode": 1,
            }

    def _extract_summary_from_output(self, output: str) -> str:
        """Extract the usage summary section from copilot output.

        The summary typically starts with 'Total usage' and includes
        duration and code change statistics.
        """
        summary_lines = []
        in_summary = False

        for line in output.splitlines():
            # Summary starts with "Total usage" or "Total duration"
            if "Total usage" in line or "Total duration" in line:
                in_summary = True
            if in_summary:
                summary_lines.append(line)

        return "\n".join(summary_lines) if summary_lines else ""

    async def _get_diff(self, worktree_path: Path) -> str:
        """Get git diff for the worktree."""
        import asyncio

        try:
            # Try staged diff first
            process = await asyncio.create_subprocess_exec(
                "git", "diff", "--cached", "--stat",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0
            )

            if process.returncode == 0 and stdout:
                return stdout.decode()

            # Try unstaged diff
            process = await asyncio.create_subprocess_exec(
                "git", "diff", "--stat",
                cwd=worktree_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0
            )
            return stdout.decode() if stdout else ""

        except (OSError, asyncio.TimeoutError) as e:
            logger.debug("Failed to get worktree diff for %s: %s", worktree_path, e)
            return ""

    async def validate_installation(self) -> bool:
        """Check if Copilot CLI is installed and accessible."""
        import asyncio

        try:
            process = await asyncio.create_subprocess_exec(
                self.config.get("command", "copilot"), "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=10.0)
            return process.returncode == 0
        except (OSError, FileNotFoundError, asyncio.TimeoutError) as e:
            logger.debug("Copilot CLI validation failed: %s", e)
            return False
