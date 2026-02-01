"""
IPC-based Progenitor Client for Backend.

Uses socket-based IPC to communicate with the Progenitor agent,
providing better reliability, streaming, and responsiveness than
the subprocess stdin/stdout approach.

Features:
- Socket-based communication (no pipe buffering issues)
- Native streaming support
- Automatic reconnection
- Optional auto-start of Progenitor subprocess
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from ..progenitor.ipc import IPCClient
from .config import ChatServerConfig

logger = logging.getLogger(__name__)


class IPCProgenitorClient:
    """Client that communicates with Progenitor via socket IPC.

    This is the recommended client for production use as it provides
    better reliability and streaming support than subprocess stdin/stdout.
    """

    def __init__(self, config: ChatServerConfig):
        """Initialize the IPC-based client.

        Args:
            config: Backend configuration.
        """
        self.config = config
        self._ipc_client: Optional[IPCClient] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._started = False
        self._lock = asyncio.Lock()
        self._stderr_log = None

    async def start(self) -> None:
        """Start the client and optionally the Progenitor subprocess."""
        if self._started:
            return

        async with self._lock:
            if self._started:
                return

            # Optionally start Progenitor subprocess with IPC enabled
            if self.config.progenitor_ipc_auto_start:
                await self._start_progenitor_subprocess()

            # Create IPC client
            self._ipc_client = IPCClient(
                host=self.config.progenitor_ipc_host,
                port=self.config.progenitor_ipc_port,
                timeout=self.config.progenitor_timeout,
                auto_reconnect=True,
                max_reconnect_attempts=5,
            )

            # Wait for connection (with retries for subprocess startup)
            connected = False
            for attempt in range(10):  # Try for up to 10 seconds
                try:
                    connected = await self._ipc_client.connect()
                    if connected:
                        break
                except Exception as e:
                    logger.debug("Connection attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(1)

            if not connected:
                raise RuntimeError(
                    f"Failed to connect to Progenitor IPC server at "
                    f"{self.config.progenitor_ipc_host}:{self.config.progenitor_ipc_port}"
                )

            self._started = True
            logger.info("IPC Progenitor client started")

    async def _start_progenitor_subprocess(self) -> None:
        """Start the Progenitor subprocess with IPC enabled."""
        script_path = self.config.progenitor_script
        if not script_path.exists():
            raise FileNotFoundError(f"Progenitor script not found: {script_path}")

        logger.info("Starting Progenitor subprocess with IPC mode")

        # Capture subprocess stderr for diagnostics
        logs_dir = Path(__file__).resolve().parents[2] / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stderr_path = logs_dir / "progenitor.ipc.stderr.log"
        self._stderr_log = open(stderr_path, "a", encoding="utf-8")

        # Start subprocess with IPC enabled
        self._process = await asyncio.create_subprocess_exec(
            self.config.progenitor_python,
            "-u",
            "-m",
            "app.progenitor.main",
            stdin=asyncio.subprocess.DEVNULL,  # No stdin needed with IPC
            stdout=asyncio.subprocess.DEVNULL,  # No stdout needed with IPC
            stderr=asyncio.subprocess.PIPE,
            cwd=script_path.parent.parent.parent,  # Project root
            env={
                **{k: v for k, v in __import__("os").environ.items()},
                "PROGENITOR_IPC_ENABLED": "true",
                "PROGENITOR_IPC_HOST": self.config.progenitor_ipc_host,
                "PROGENITOR_IPC_PORT": str(self.config.progenitor_ipc_port),
            },
        )

        logger.info(f"Progenitor started (PID: {self._process.pid})")

        # Start stderr reader task
        asyncio.create_task(self._read_stderr())

        # Wait for subprocess to be ready
        await asyncio.sleep(2)

    async def _read_stderr(self) -> None:
        """Read and log stderr from the subprocess."""
        if not self._process or not self._process.stderr:
            return

        while True:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode().strip()
                logger.error(f"[Progenitor stderr] {decoded}")
                if self._stderr_log:
                    self._stderr_log.write(decoded + "\n")
                    self._stderr_log.flush()
            except Exception as e:
                logger.error(f"Error reading stderr: {e}")
                break

    async def stop(self) -> None:
        """Stop the client and subprocess."""
        async with self._lock:
            # Disconnect IPC client
            if self._ipc_client:
                await self._ipc_client.disconnect()
                self._ipc_client = None

            # Stop subprocess
            if self._process:
                logger.info("Stopping Progenitor subprocess...")
                self._process.terminate()

                try:
                    await asyncio.wait_for(
                        self._process.wait(),
                        timeout=self.config.progenitor_stop_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Progenitor did not terminate, killing...")
                    self._process.kill()

                self._process = None

            if self._stderr_log:
                self._stderr_log.close()
                self._stderr_log = None

            self._started = False

    async def send_message(
        self,
        message: str,
        request_id: Optional[str] = None,
    ) -> str:
        """Send a message and get the response.

        Args:
            message: The user message.
            request_id: Optional request ID for correlation.

        Returns:
            The assistant's response.
        """
        if not self._started:
            await self.start()

        if not self._ipc_client:
            raise RuntimeError("IPC client not initialized")

        return await self._ipc_client.send_message(message, request_id)

    async def send_message_stream(
        self,
        message: str,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Send a message and stream the response.

        Args:
            message: The user message.
            request_id: Optional request ID for correlation.

        Yields:
            Response chunks.
        """
        if not self._started:
            await self.start()

        if not self._ipc_client:
            raise RuntimeError("IPC client not initialized")

        async for chunk in self._ipc_client.send_message_stream(message, request_id):
            yield chunk

    @property
    def is_running(self) -> bool:
        """Check if the client is running."""
        return (
            self._started
            and self._ipc_client is not None
            and self._ipc_client.is_connected
        )

    def last_heartbeat(self) -> Optional[float]:
        """Get last heartbeat timestamp."""
        if self._ipc_client:
            return self._ipc_client.last_heartbeat()
        return None

    def queue_depth(self) -> int:
        """Get queue depth (for compatibility)."""
        return 0

    def get_status(self) -> dict:
        """Get client status information."""
        status = {
            "running": self.is_running,
            "started": self._started,
            "mode": "ipc",
        }

        if self._ipc_client:
            status.update(self._ipc_client.get_status())

        if self._process:
            status["pid"] = self._process.pid
            status["subprocess_running"] = self._process.returncode is None

        return status


def get_ipc_client(config: ChatServerConfig) -> IPCProgenitorClient:
    """Create an IPC-based Progenitor client.

    Args:
        config: Backend configuration.

    Returns:
        An IPCProgenitorClient instance.
    """
    return IPCProgenitorClient(config)
