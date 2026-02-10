"""
IPC Client for connecting to Progenitor.

Used by the backend to communicate with the Progenitor agent
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
HEARTBEAT_INTERVAL = 5  # 5 seconds for responsive event delivery
HEARTBEAT_MISS_THRESHOLD = 3  # Disconnect after this many consecutive misses


class IPCClient:
    """Socket-based IPC client for communicating with Progenitor.

    Provides a reliable connection to the Progenitor IPC server with
    automatic reconnection and streaming support.

    Also receives push events from the server about background work
    (backlog processing, scheduled tasks, etc.).
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
        self._request_in_progress = False  # Flag to pause heartbeats during requests
        self._missed_heartbeats = 0  # Track consecutive missed heartbeats

        # Event handling - queue for received events and optional callback
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._event_callback: Optional[Any] = None  # Callable[[dict], Awaitable[None]]

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
            except Exception as e:
                logger.debug("Error closing writer (expected during shutdown): %s", e)
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
            attempt = self._reconnect_count
            delay = self._reconnect_delay
            logger.info(
                "Reconnection attempt %d/%d in %.1fs",
                attempt,
                self.max_reconnect_attempts,
                delay,
            )

            await asyncio.sleep(delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, RECONNECT_DELAY_MAX
            )

            if await self.connect():
                logger.info("Reconnected to Progenitor IPC server (attempt %d)", attempt)
                return

        raise ConnectionError(
            f"Failed to reconnect after {self.max_reconnect_attempts} attempts"
        )

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats and poll for events."""
        heartbeat_timer = 0.0
        poll_interval = 0.5  # Check for events every 0.5 seconds

        while self._connected:
            try:
                await asyncio.sleep(poll_interval)
                heartbeat_timer += poll_interval

                if not self._connected or not self._writer:
                    break

                # Skip operations if we're in the middle of streaming or a request
                if self._streaming or self._request_in_progress:
                    self._missed_heartbeats = 0  # Reset counter - we're active
                    heartbeat_timer = 0  # Reset heartbeat timer too
                    continue

                # Try to read any pending events (non-blocking check)
                async with self._lock:
                    if not self._connected or not self._reader:
                        break

                    # Skip if now streaming/in request (after lock)
                    if self._streaming or self._request_in_progress:
                        continue

                    # Quick non-blocking check for events
                    try:
                        msg = await read_message(self._reader, timeout=0.1)
                        if msg and msg.type == MessageType.AGENT_EVENT:
                            await self._handle_event(msg)
                            self._last_activity = time.time()
                    except asyncio.TimeoutError:
                        pass  # No events pending, that's fine

                # Send heartbeat only when timer exceeds interval
                if heartbeat_timer < HEARTBEAT_INTERVAL:
                    continue

                heartbeat_timer = 0  # Reset timer

                # Skip heartbeat if there was recent activity (within 2x interval)
                time_since_activity = time.time() - self._last_activity
                if time_since_activity < HEARTBEAT_INTERVAL * 2:
                    self._missed_heartbeats = 0
                    continue

                # Acquire lock for heartbeat - prevents conflict with send_message
                async with self._lock:
                    if not self._connected or not self._writer or not self._reader:
                        break

                    # Double-check streaming/request state after acquiring lock
                    if self._streaming or self._request_in_progress:
                        continue

                    # Send heartbeat
                    await write_message(self._writer, heartbeat())

                    # Wait for ack, but also handle any events that arrive
                    while True:
                        msg = await read_message(self._reader, timeout=10)
                        if msg is None:
                            self._missed_heartbeats += 1
                            break

                        # Handle events that might arrive during heartbeat
                        if msg.type == MessageType.AGENT_EVENT:
                            await self._handle_event(msg)
                            continue  # Keep waiting for heartbeat ack

                        if msg.type == MessageType.HEARTBEAT_ACK:
                            self._last_activity = time.time()
                            self._missed_heartbeats = 0
                            break
                        else:
                            # Unexpected message type during heartbeat
                            logger.debug("Unexpected message during heartbeat: %s", msg.type)
                            self._missed_heartbeats += 1
                            break

                    if self._missed_heartbeats > 0:
                        logger.debug("Heartbeat issue (miss %d/%d)",
                                    self._missed_heartbeats, HEARTBEAT_MISS_THRESHOLD)
                        if self._missed_heartbeats >= HEARTBEAT_MISS_THRESHOLD:
                            logger.warning("Too many missed heartbeats, connection may be stale")
                            # Don't disconnect yet - let the next request attempt reconnect

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Heartbeat error (may be normal during requests): %s", e)
                self._missed_heartbeats += 1
                if self._missed_heartbeats >= HEARTBEAT_MISS_THRESHOLD:
                    logger.warning("Heartbeat errors exceeded threshold, marking disconnected")
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
        for attempt in range(2):
            await self._ensure_connected()

            # Pause heartbeats during message exchange
            self._streaming = True
            self._request_in_progress = True
            self._missed_heartbeats = 0  # Reset on new request

            async with self._lock:
                if not self._writer or not self._reader:
                    self._streaming = False
                    self._request_in_progress = False
                    raise ConnectionError("Not connected")

                try:
                    # Send user message (non-streaming)
                    await write_message(
                        self._writer,
                        user_message(message, request_id, stream=False),
                    )

                    # Read response (may receive events while waiting)
                    while True:
                        msg = await read_message(self._reader, timeout=self.timeout)

                        if msg is None:
                            self._connected = False
                            raise ConnectionError("Connection closed by server")

                        self._last_activity = time.time()

                        # Handle events that arrive during message exchange
                        if msg.type == MessageType.AGENT_EVENT:
                            await self._handle_event(msg)
                            continue  # Keep waiting for actual response

                        if msg.type == MessageType.ERROR:
                            raise RuntimeError(f"Progenitor error: {msg.content}")

                        if msg.type == MessageType.ASSISTANT_MESSAGE:
                            return msg.content

                        # Unexpected message type - log and use content
                        logger.warning("Unexpected message type: %s", msg.type)
                        return msg.content or ""

                except asyncio.TimeoutError:
                    raise TimeoutError(f"Request timed out after {self.timeout}s")
                except ConnectionError as e:
                    self._connected = False
                    if attempt == 0 and self.auto_reconnect:
                        logger.warning("Connection lost during request, retrying")
                        continue
                    raise
                except Exception as e:
                    if "Connection" in str(type(e).__name__):
                        self._connected = False
                    raise
                finally:
                    # Resume heartbeats
                    self._streaming = False
                    self._request_in_progress = False

            raise ConnectionError("Failed to send message after retries")

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
        for attempt in range(2):
            await self._ensure_connected()

            # Pause heartbeats BEFORE acquiring lock to prevent race condition
            self._streaming = True
            self._request_in_progress = True
            self._missed_heartbeats = 0  # Reset on new request

            # CRITICAL: Hold lock for ENTIRE request-response cycle.
            # Without this, concurrent requests would interleave on the socket,
            # causing responses to be delivered to the wrong caller.
            async with self._lock:
                try:
                    if not self._writer or not self._reader:
                        raise ConnectionError("Not connected")

                    # Send user message (streaming)
                    await write_message(
                        self._writer,
                        user_message(message, request_id, stream=True),
                    )

                    # Read stream chunks (may receive events while streaming)
                    while True:
                        msg = await read_message(self._reader, timeout=self.timeout)

                        if msg is None:
                            self._connected = False
                            raise ConnectionError("Connection closed during streaming")

                        self._last_activity = time.time()

                        # Handle events that arrive during streaming
                        if msg.type == MessageType.AGENT_EVENT:
                            await self._handle_event(msg)
                            continue  # Keep reading stream

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

                    break

                except asyncio.TimeoutError:
                    raise TimeoutError(f"Streaming timed out after {self.timeout}s")
                except ConnectionError:
                    self._connected = False
                    if attempt == 0 and self.auto_reconnect:
                        logger.warning("Connection lost during streaming, retrying")
                        continue
                    raise
                except Exception as e:
                    if "Connection" in str(type(e).__name__):
                        self._connected = False
                    raise
                finally:
                    # Resume heartbeats after streaming completes
                    self._streaming = False
                    self._request_in_progress = False

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
            "pending_events": self._event_queue.qsize(),
        }

    def set_event_callback(self, callback: Optional[Any]) -> None:
        """Set a callback to be called when events are received.

        Args:
            callback: Async callable that takes an event dict.
                     Set to None to disable callback.
        """
        self._event_callback = callback

    async def get_event(self, timeout: Optional[float] = None) -> Optional[dict[str, Any]]:
        """Get an event from the event queue.

        Events are pushed by the server and represent background work updates
        (e.g., backlog processing, scheduled task execution).

        Args:
            timeout: Max seconds to wait. None = wait forever.

        Returns:
            Event dict with 'event_type', 'content', 'metadata', and 'timestamp',
            or None if timed out.
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=timeout
                )
            return await self._event_queue.get()
        except asyncio.TimeoutError:
            return None

    async def _handle_event(self, msg: IPCMessage) -> None:
        """Handle an incoming AGENT_EVENT message.

        Args:
            msg: The event message from the server.
        """
        event = {
            "event_type": msg.event_type or "unknown",
            "content": msg.content,
            "metadata": msg.metadata or {},
            "timestamp": msg.timestamp,
        }

        # Call callback if set
        if self._event_callback:
            try:
                await self._event_callback(event)
            except Exception as e:
                logger.warning("Event callback error: %s", e)

        # Also queue the event for polling
        try:
            self._event_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Event queue full, dropping event: %s", event.get("event_type"))


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
