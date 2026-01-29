"""
IPC Client for connecting to Progenitor.

Used by the chat_server to communicate with the Progenitor agent
via socket-based IPC instead of stdin/stdout.

Features:
- Automatic connection management
- Reconnection with exponential backoff
- Streaming response support
- Heartbeat monitoring
- Request timeout handling
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Optional, Any

from .protocol import (
    MessageType,
    IPCMessage,
    read_message,
    write_message,
    user_message,
    heartbeat,
    shutdown_message,
)

logger = logging.getLogger(__name__)

# Client defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9500
DEFAULT_TIMEOUT = 300  # 5 minutes
RECONNECT_DELAY_INITIAL = 1.0
RECONNECT_DELAY_MAX = 30.0
HEARTBEAT_INTERVAL = 30


class IPCClient:
    """Socket-based IPC client for communicating with Progenitor.

    Provides a reliable connection to the Progenitor IPC server with
    automatic reconnection and streaming support.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 5,
    ):
        """Initialize the IPC client.

        Args:
            host: Server host.
            port: Server port.
            timeout: Request timeout in seconds.
            auto_reconnect: Whether to auto-reconnect on connection loss.
            max_reconnect_attempts: Maximum reconnection attempts.
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max_reconnect_attempts

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._last_activity = 0.0
        self._reconnect_count = 0
        self._reconnect_delay = RECONNECT_DELAY_INITIAL
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._streaming = False  # Flag to pause heartbeats during streaming

    async def connect(self) -> bool:
        """Connect to the Progenitor IPC server.

        Returns:
            True if connection successful, False otherwise.
        """
        if self._connected:
            return True

        async with self._lock:
            if self._connected:
                return True

            try:
                logger.info("Connecting to Progenitor at %s:%d", self.host, self.port)

                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=10,
                )

                # Wait for ready message
                msg = await read_message(self._reader, timeout=10)
                if msg is None or msg.type != MessageType.READY:
                    raise ConnectionError("Did not receive ready message from server")

                self._connected = True
                self._last_activity = time.time()
                self._reconnect_count = 0
                self._reconnect_delay = RECONNECT_DELAY_INITIAL

                # Start heartbeat task
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                logger.info("Connected to Progenitor IPC server")
                return True

            except Exception as e:
                logger.error("Failed to connect to Progenitor: %s", e)
                await self._cleanup_connection()
                return False

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        async with self._lock:
            await self._cleanup_connection()

    async def _cleanup_connection(self) -> None:
        """Clean up connection resources."""
        # Cancel heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close connection
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        self._reader = None
        self._connected = False

    async def _ensure_connected(self) -> None:
        """Ensure we're connected, reconnecting if necessary."""
        if self._connected:
            return

        if not self.auto_reconnect:
            raise ConnectionError("Not connected to Progenitor")

        # Attempt reconnection with backoff
        while self._reconnect_count < self.max_reconnect_attempts:
            self._reconnect_count += 1
            logger.info(
                "Reconnection attempt %d/%d in %.1fs",
                self._reconnect_count,
                self.max_reconnect_attempts,
                self._reconnect_delay,
            )

            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_DELAY_MAX
            )

            if await self.connect():
                return

        raise ConnectionError(
            f"Failed to reconnect after {self.max_reconnect_attempts} attempts"
        )

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep connection alive."""
        while self._connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                if not self._connected or not self._writer:
                    break

                # Acquire lock for heartbeat - prevents conflict with send_message
                async with self._lock:
                    if not self._connected or not self._writer or not self._reader:
                        break

                    # Skip heartbeat if we're in the middle of streaming
                    # (another operation has priority)
                    if self._streaming:
                        continue

                    # Send heartbeat
                    await write_message(self._writer, heartbeat())

                    # Wait for ack
                    msg = await read_message(self._reader, timeout=10)
                    if msg and msg.type == MessageType.HEARTBEAT_ACK:
                        self._last_activity = time.time()
                    else:
                        logger.warning("No heartbeat ack received")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: %s", e)
                self._connected = False
                break

    async def send_message(
        self,
        message: str,
        request_id: Optional[str] = None,
    ) -> str:
        """Send a message and get the complete response.

        Args:
            message: The user message to send.
            request_id: Optional request ID for correlation.

        Returns:
            The assistant's response.

        Raises:
            ConnectionError: If not connected and reconnection fails.
            TimeoutError: If request times out.
        """
        await self._ensure_connected()

        # Pause heartbeats during message exchange
        self._streaming = True

        async with self._lock:
            if not self._writer or not self._reader:
                self._streaming = False
                raise ConnectionError("Not connected")

            try:
                # Send user message (non-streaming)
                await write_message(
                    self._writer,
                    user_message(message, request_id, stream=False),
                )

                # Read response
                msg = await read_message(self._reader, timeout=self.timeout)

                if msg is None:
                    self._connected = False
                    raise ConnectionError("Connection closed by server")

                self._last_activity = time.time()

                if msg.type == MessageType.ERROR:
                    raise RuntimeError(f"Progenitor error: {msg.content}")

                if msg.type == MessageType.ASSISTANT_MESSAGE:
                    return msg.content

                # Unexpected message type
                logger.warning("Unexpected message type: %s", msg.type)
                return msg.content or ""

            except asyncio.TimeoutError:
                raise TimeoutError(f"Request timed out after {self.timeout}s")
            except Exception as e:
                if "Connection" in str(type(e).__name__):
                    self._connected = False
                raise
            finally:
                # Resume heartbeats
                self._streaming = False

    async def send_message_stream(
        self,
        message: str,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Send a message and stream the response.

        Args:
            message: The user message to send.
            request_id: Optional request ID for correlation.

        Yields:
            Response chunks as they arrive.

        Raises:
            ConnectionError: If not connected and reconnection fails.
            TimeoutError: If request times out.
        """
        await self._ensure_connected()

        # Pause heartbeats BEFORE acquiring lock to prevent race condition
        self._streaming = True

        # Don't hold lock during streaming - just for sending
        async with self._lock:
            if not self._writer or not self._reader:
                self._streaming = False
                raise ConnectionError("Not connected")

            # Send user message (streaming)
            await write_message(
                self._writer,
                user_message(message, request_id, stream=True),
            )

        # Read stream chunks (without lock - heartbeat is paused via _streaming flag)
        try:
            while True:
                msg = await read_message(self._reader, timeout=self.timeout)

                if msg is None:
                    self._connected = False
                    raise ConnectionError("Connection closed during streaming")

                self._last_activity = time.time()

                if msg.type == MessageType.ERROR:
                    raise RuntimeError(f"Progenitor error: {msg.content}")

                if msg.type == MessageType.STREAM_CHUNK:
                    if msg.content:
                        yield msg.content
                    if msg.is_final:
                        break

                elif msg.type == MessageType.STREAM_END:
                    break

                elif msg.type == MessageType.ASSISTANT_MESSAGE:
                    # Fallback: got complete message instead of stream
                    yield msg.content
                    break

        except asyncio.TimeoutError:
            raise TimeoutError(f"Streaming timed out after {self.timeout}s")
        except Exception as e:
            if "Connection" in str(type(e).__name__):
                self._connected = False
            raise
        finally:
            # Resume heartbeats after streaming completes
            self._streaming = False

    async def request_shutdown(self) -> None:
        """Request the Progenitor server to shutdown."""
        if not self._connected or not self._writer:
            return

        try:
            await write_message(self._writer, shutdown_message())
        except Exception as e:
            logger.error("Error sending shutdown request: %s", e)

    @property
    def is_connected(self) -> bool:
        """Check if connected to the server."""
        return self._connected

    def last_heartbeat(self) -> Optional[float]:
        """Get the timestamp of last successful activity."""
        return self._last_activity if self._last_activity > 0 else None

    def get_status(self) -> dict[str, Any]:
        """Get client status information."""
        return {
            "connected": self._connected,
            "host": self.host,
            "port": self.port,
            "last_activity_ago": (
                time.time() - self._last_activity
                if self._last_activity > 0 else None
            ),
            "reconnect_count": self._reconnect_count,
            "max_reconnect_attempts": self.max_reconnect_attempts,
        }


# Convenience function for creating clients
def create_ipc_client(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> IPCClient:
    """Create an IPC client with default settings.

    Args:
        host: Server host.
        port: Server port.
        timeout: Request timeout.

    Returns:
        Configured IPCClient instance.
    """
    return IPCClient(host=host, port=port, timeout=timeout)
