"""
Progenitor Client - Subprocess communication with the Progenitor agent.

Spawns Progenitor as a subprocess and communicates via stdin/stdout.

CR-001 Improvements:
- Message framing with length prefix for robust parsing
- Heartbeat/liveness checking
- Automatic restart on process crash
- Configurable timeouts
"""

import asyncio
import contextlib
import json
import logging
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO, Any, cast

from .config import ChatServerConfig

logger = logging.getLogger(__name__)

# Message framing constants
MESSAGE_DELIMITER = b"\n---PROGENITOR_MSG_END---\n"
HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 10  # seconds


class ProgenitorClient:
    """Client for communicating with Progenitor via subprocess.

    CR-001: Enhanced with heartbeat monitoring and auto-restart.
    """

    def __init__(self, config: ChatServerConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._stderr_log: IO[str] | None = None
        self._log_stack: contextlib.ExitStack | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._last_heartbeat: float = 0
        self._restart_count: int = 0
        self._max_restarts: int = 3
        self._restart_delay: float = 1.0

    def _open_stderr_log(self) -> tuple[contextlib.ExitStack | None, IO[str] | None]:
        """Open a writable stderr log file, falling back outside the checkout if needed."""
        repo_logs_dir = Path(__file__).resolve().parents[2] / "logs"
        fallback_logs_dir = Path(tempfile.gettempdir()) / "progenitor-logs"
        last_error: OSError | None = None

        for logs_dir in (repo_logs_dir, fallback_logs_dir):
            try:
                logs_dir.mkdir(parents=True, exist_ok=True)
                stderr_path = logs_dir / "progenitor.client.stderr.log"
                log_stack = contextlib.ExitStack()
                stderr_log = log_stack.enter_context(stderr_path.open("a", encoding="utf-8"))
                if logs_dir == fallback_logs_dir:
                    logger.warning(
                        "Repo logs directory is not writable; using fallback stderr log at %s",
                        stderr_path,
                    )
                return log_stack, stderr_log
            except OSError as exc:
                last_error = exc

        logger.warning(
            "Unable to open progenitor stderr log file; continuing without file logging: %s",
            last_error,
        )
        return None, None

    async def start(self) -> None:
        """Start the Progenitor subprocess."""
        if self._started:
            return

        async with self._lock:
            if self._started:
                return

            script_path = self.config.progenitor_script
            if not script_path.exists():
                raise FileNotFoundError(f"Progenitor script not found: {script_path}")

            logger.info(f"Starting Progenitor subprocess: {script_path}")

            # Capture subprocess stdout/stderr to files for diagnostics
            self._log_stack, self._stderr_log = self._open_stderr_log()

            # Use module invocation to preserve package-relative imports
            self._process = await asyncio.create_subprocess_exec(
                self.config.progenitor_python,
                "-u",
                "-m",
                "app.progenitor.main",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=script_path.parent.parent.parent,  # Project root
            )

            self._started = True
            self._last_heartbeat = time.time()
            self._restart_count = 0
            logger.info(f"Progenitor started (PID: {self._process.pid})")

            # Start stderr reader task
            asyncio.create_task(self._read_stderr())

            # Start heartbeat monitoring (CR-001)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

    async def _heartbeat_monitor(self) -> None:
        """Monitor process health with periodic heartbeat checks (CR-001)."""
        while self._started:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                if not self._check_process_alive():
                    logger.warning("Progenitor process died, attempting restart...")
                    await self._attempt_restart()
                    continue

                # Check if we've received any activity recently
                time_since_heartbeat = time.time() - self._last_heartbeat
                if time_since_heartbeat > HEARTBEAT_INTERVAL * 3:
                    logger.warning(
                        "No activity from Progenitor in %.1fs, checking health...",
                        time_since_heartbeat,
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat monitor error: %s", e)

    def _check_process_alive(self) -> bool:
        """Check if the subprocess is still running (CR-001)."""
        if not self._process:
            return False
        return self._process.returncode is None

    async def _attempt_restart(self) -> bool:
        """Attempt to restart the Progenitor process (CR-001)."""
        if self._restart_count >= self._max_restarts:
            logger.error("Max restart attempts (%d) reached, giving up", self._max_restarts)
            return False

        self._restart_count += 1
        logger.info(
            "Restart attempt %d/%d after %.1fs delay",
            self._restart_count,
            self._max_restarts,
            self._restart_delay,
        )

        await asyncio.sleep(self._restart_delay)
        self._restart_delay *= 2  # Exponential backoff

        # Reset state and restart
        self._started = False
        self._process = None

        try:
            await self.start()
            logger.info("Progenitor restarted successfully")
            return True
        except Exception as e:
            logger.error("Failed to restart Progenitor: %s", e)
            return False

    async def _read_stderr(self) -> None:
        """Read and log stderr from the subprocess."""
        if not self._process or not self._process.stderr:
            return

        while True:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break
                # Surface progenitor stderr so crashes are visible in chatserver logs
                decoded = line.decode().strip()
                logger.error(f"[Progenitor stderr] {decoded}")
                if self._stderr_log:
                    self._stderr_log.write(decoded + "\n")
                    self._stderr_log.flush()
            except Exception as e:
                logger.error(f"Error reading stderr: {e}")
                break

    async def stop(self) -> None:
        """Stop the Progenitor subprocess."""
        async with self._lock:
            # Cancel heartbeat task first (CR-001)
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None

            if self._process:
                logger.info("Stopping Progenitor subprocess...")
                self._process.terminate()

                # Use configurable timeout (CR-001: CQ-011 fix)
                stop_timeout = getattr(self.config, "progenitor_stop_timeout", 5)
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=stop_timeout)
                except TimeoutError:
                    logger.warning("Progenitor did not terminate in %ds, killing...", stop_timeout)
                    self._process.kill()
                self._process = None
                self._started = False
                if self._stderr_log:
                    self._stderr_log.close()
                    self._stderr_log = None
                if self._log_stack:
                    self._log_stack.close()
                    self._log_stack = None

    async def send_message(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Send a message to Progenitor and get the response.

        Args:
            message: The user message to send
            request_id: Optional request ID for correlation

        Returns:
            The response from Progenitor
        """
        if not self._started:
            await self.start()

        # CR-001: Check process health before sending
        if not self._check_process_alive():
            logger.warning("Progenitor process not alive, restarting...")
            if not await self._attempt_restart():
                raise RuntimeError("Progenitor process not running and restart failed")

        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Progenitor process not running")

        async with self._lock:
            # Send message as JSON line
            payload: dict[str, Any] = {
                "type": "user_message",
                "content": message,
            }
            if request_id:
                payload["request_id"] = request_id
            if metadata:
                payload["metadata"] = metadata

            self._process.stdin.write((json.dumps(payload) + "\n").encode())
            await self._process.stdin.drain()

            # Read response
            response_line: bytes | None = None
            try:
                response_line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=self.config.progenitor_timeout,
                )

                if not response_line:
                    raise RuntimeError("Progenitor process closed unexpectedly")

                # CR-001: Update heartbeat timestamp on successful communication
                self._last_heartbeat = time.time()

                response = json.loads(response_line.decode())
                return cast(str, response.get("content", str(response)))

            except TimeoutError:
                raise TimeoutError(
                    f"Progenitor did not respond within {self.config.progenitor_timeout}s"
                )
            except json.JSONDecodeError:
                # Return raw text if not JSON
                self._last_heartbeat = time.time()  # CR-001
                return response_line.decode().strip() if response_line else ""

    async def send_message_stream_events(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a message and stream structured events from the subprocess client."""
        if not self._started:
            await self.start()

        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Progenitor process not running")

        async with self._lock:
            # Send message as JSON line with stream flag
            payload: dict[str, Any] = {
                "type": "user_message",
                "content": message,
                "stream": True,
            }
            if request_id:
                payload["request_id"] = request_id
            if metadata:
                payload["metadata"] = metadata

            self._process.stdin.write((json.dumps(payload) + "\n").encode())
            await self._process.stdin.drain()

            # Read streaming chunks until we get the final response
            try:
                while True:
                    response_line = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self.config.progenitor_timeout,
                    )

                    if not response_line:
                        raise RuntimeError("Progenitor process closed unexpectedly")

                    try:
                        response = json.loads(response_line.decode())
                    except json.JSONDecodeError:
                        # Non-JSON output, yield as a synthetic stream chunk
                        text = response_line.decode().strip()
                        if text:
                            yield {
                                "type": "stream_chunk",
                                "content": text,
                                "is_final": False,
                                "request_id": request_id,
                            }
                        continue

                    msg_type = response.get("type", "")
                    content = response.get("content", "")

                    if msg_type == "stream_chunk":
                        yield {
                            "type": "stream_chunk",
                            "content": content,
                            "is_final": bool(response.get("is_final", False)),
                            "request_id": response.get("request_id", request_id),
                        }
                        if response.get("is_final", False):
                            break

                    elif msg_type == "stream_event":
                        yield {
                            "type": "stream_event",
                            "event_type": response.get("event_type", "unknown"),
                            "content": content,
                            "metadata": response.get("metadata", {}),
                            "request_id": response.get("request_id", request_id),
                        }

                    elif msg_type == "stream_end":
                        # Explicit stream end signal
                        break

                    else:
                        # Unknown message type - log and continue
                        logger.debug("Received unknown message type: %s", msg_type)

            except TimeoutError:
                raise TimeoutError(
                    f"Progenitor did not respond within {self.config.progenitor_timeout}s"
                )

    @property
    def is_running(self) -> bool:
        """Check if Progenitor is running."""
        return self._started and self._process is not None and self._process.returncode is None

    def get_status(self) -> dict:
        """Get detailed client status (CR-001)."""
        return {
            "running": self.is_running,
            "started": self._started,
            "pid": self._process.pid if self._process else None,
            "last_heartbeat_ago": (
                time.time() - self._last_heartbeat if self._last_heartbeat > 0 else None
            ),
            "restart_count": self._restart_count,
            "max_restarts": self._max_restarts,
        }


# Global client instance
_client: ProgenitorClient | None = None


def get_client(config: ChatServerConfig | None = None) -> ProgenitorClient:
    """Get or create the global Progenitor client."""
    global _client
    if _client is None:
        if config is None:
            config = ChatServerConfig.from_env()
        _client = ProgenitorClient(config)
    return _client
