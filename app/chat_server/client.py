"""
Progenitor Client - Subprocess communication with the Progenitor agent.

Spawns Progenitor as a subprocess and communicates via stdin/stdout.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Optional

from .config import ChatServerConfig

logger = logging.getLogger(__name__)


class ProgenitorClient:
    """Client for communicating with Progenitor via subprocess."""

    def __init__(self, config: ChatServerConfig):
        self.config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()
        self._started = False
        self._stderr_log = None

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
            logs_dir = Path(__file__).resolve().parents[2] / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            stderr_path = logs_dir / "progenitor.client.stderr.log"
            self._stderr_log = open(stderr_path, "a", encoding="utf-8")

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
            logger.info(f"Progenitor started (PID: {self._process.pid})")

            # Start stderr reader task
            asyncio.create_task(self._read_stderr())

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
            if self._process:
                logger.info("Stopping Progenitor subprocess...")
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("Progenitor did not terminate gracefully, killing...")
                    self._process.kill()
                self._process = None
                self._started = False
                if self._stderr_log:
                    self._stderr_log.close()
                    self._stderr_log = None

    async def send_message(self, message: str) -> str:
        """Send a message to Progenitor and get the response.

        Args:
            message: The user message to send

        Returns:
            The response from Progenitor
        """
        if not self._started:
            await self.start()

        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Progenitor process not running")

        async with self._lock:
            # Send message as JSON line
            payload = json.dumps({"type": "user_message", "content": message})
            self._process.stdin.write((payload + "\n").encode())
            await self._process.stdin.drain()

            # Read response
            try:
                response_line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=self.config.progenitor_timeout,
                )

                if not response_line:
                    raise RuntimeError("Progenitor process closed unexpectedly")

                response = json.loads(response_line.decode())
                return response.get("content", str(response))

            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Progenitor did not respond within {self.config.progenitor_timeout}s"
                )
            except json.JSONDecodeError as e:
                # Return raw text if not JSON
                return response_line.decode().strip()

    async def send_message_stream(
        self, message: str
    ) -> AsyncIterator[str]:
        """Send a message and stream the response.

        For now, this just wraps send_message since Progenitor
        doesn't support streaming yet.
        """
        response = await self.send_message(message)
        # Yield in chunks to simulate streaming
        words = response.split()
        for i, word in enumerate(words):
            yield word + (" " if i < len(words) - 1 else "")
            await asyncio.sleep(0.02)  # Small delay for streaming effect

    @property
    def is_running(self) -> bool:
        """Check if Progenitor is running."""
        return (
            self._started
            and self._process is not None
            and self._process.returncode is None
        )


# Global client instance
_client: Optional[ProgenitorClient] = None


def get_client(config: Optional[ChatServerConfig] = None) -> ProgenitorClient:
    """Get or create the global Progenitor client."""
    global _client
    if _client is None:
        if config is None:
            config = ChatServerConfig.from_env()
        _client = ProgenitorClient(config)
    return _client
