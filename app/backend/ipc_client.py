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
- Event forwarding from agent to frontend via SSE
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..progenitor.ipc import IPCClient
from .config import ChatServerConfig

logger = logging.getLogger(__name__)

# Type for event callback (will be ui_routes.broadcast_chat_event)
EventCallback = Callable[[str, dict], None]


class IPCProgenitorClient:
    """Client that communicates with Progenitor via socket IPC.

    This is the recommended client for production use as it provides
    better reliability and streaming support than subprocess stdin/stdout.

    Also forwards agent events (scheduled_response, agent_message) to the
    frontend via SSE by calling the provided event callback.
    """

    def __init__(self, config: ChatServerConfig, event_callback: EventCallback | None = None):
        """Initialize the IPC-based client.

        Args:
            config: Backend configuration.
            event_callback: Optional callback for forwarding events to SSE.
                           Signature: (event_type: str, data: dict) -> None
        """
        self.config = config
        self._ipc_client: IPCClient | None = None
        self._started = False
        self._lock = asyncio.Lock()
        self._event_callback = event_callback
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_log: Any = None

    async def start(self) -> None:
        """Start the client and optionally the Progenitor subprocess."""
        if self._started:
            return

        async with self._lock:
            if self._started:
                return

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

            # Set up event callback on the underlying IPC client for immediate forwarding
            if self._event_callback:
                # Capture callback in local variable to avoid None check issues
                callback = self._event_callback

                async def forward_event(event: dict) -> None:
                    """Forward events from IPC to SSE broadcast."""
                    event_type = event.get("event_type", "unknown")
                    content = event.get("content", "")
                    metadata = event.get("metadata", {})
                    timestamp = event.get("timestamp")

                    logger.info(
                        "Forwarding event to SSE: type=%s, content=%s",
                        event_type,
                        content[:100] if content else "",
                    )

                    try:
                        callback(
                            event_type,
                            {
                                "content": content,
                                "metadata": metadata,
                                "timestamp": timestamp,
                            },
                        )
                    except Exception as e:
                        logger.warning("Error in event callback: %s", e)

                self._ipc_client.set_event_callback(forward_event)
                logger.info("Event forwarding callback configured")

            self._started = True
            logger.info("IPC Progenitor client started")

    async def stop(self) -> None:
        """Stop the client and subprocess."""
        async with self._lock:
            # Clear event callback
            if self._ipc_client:
                self._ipc_client.set_event_callback(None)
                logger.info("Event forwarding callback cleared")

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
                except TimeoutError:
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
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
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

        chunks: list[str] = []
        async for event in self._ipc_client.send_message_stream_events(
            message, request_id, metadata
        ):
            if event.get("type") != "stream_chunk":
                continue
            chunk_text = str(event.get("content", ""))
            if chunk_text:
                chunks.append(chunk_text)
        return "".join(chunks)

    async def send_message_stream_events(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a message and stream structured IPC events."""
        if not self._started:
            await self.start()

        if not self._ipc_client:
            raise RuntimeError("IPC client not initialized")

        async for event in self._ipc_client.send_message_stream_events(
            message, request_id, metadata
        ):
            yield event

    @property
    def is_running(self) -> bool:
        """Check if the client is running."""
        return self._started and self._ipc_client is not None and self._ipc_client.is_connected

    def last_heartbeat(self) -> float | None:
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
            "event_forwarding": self._event_callback is not None,
        }

        if self._ipc_client:
            status.update(self._ipc_client.get_status())

        return status

    def set_event_callback(self, callback: EventCallback | None) -> None:
        """Set the event callback for SSE forwarding.

        Args:
            callback: Callback function to forward events.
                     Signature: (event_type: str, data: dict) -> None
        """
        self._event_callback = callback
        # Also update the underlying IPC client if it exists
        if self._ipc_client and callback:

            async def forward_event(event: dict) -> None:
                """Forward events from IPC to SSE broadcast."""
                event_type = event.get("event_type", "unknown")
                content = event.get("content", "")
                metadata = event.get("metadata", {})
                timestamp = event.get("timestamp")

                logger.info(
                    "Forwarding event to SSE: type=%s, content=%s",
                    event_type,
                    content[:100] if content else "",
                )

                try:
                    callback(
                        event_type,
                        {
                            "content": content,
                            "metadata": metadata,
                            "timestamp": timestamp,
                        },
                    )
                except Exception as e:
                    logger.warning("Error in event callback: %s", e)

            self._ipc_client.set_event_callback(forward_event)


def get_ipc_client(
    config: ChatServerConfig, event_callback: EventCallback | None = None
) -> IPCProgenitorClient:
    """Create an IPC-based Progenitor client.

    Args:
        config: Backend configuration.
        event_callback: Optional callback for forwarding events to SSE.

    Returns:
        An IPCProgenitorClient instance.
    """
    return IPCProgenitorClient(config, event_callback=event_callback)


async def start_ipc_client_with_retry(
    client: IPCProgenitorClient,
    *,
    retry_delay: float = 5.0,
) -> None:
    """Start an IPC client in the background and keep retrying until it connects."""
    attempts = 0

    while True:
        try:
            await client.start()
            if attempts:
                logger.info("IPC event-forwarding client connected after %d retries", attempts)
            else:
                logger.info("IPC event-forwarding client connected")
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            attempts += 1
            log = logger.warning if attempts == 1 or attempts % 12 == 0 else logger.debug
            log("IPC event-forwarding client unavailable, retrying in %.1fs: %s", retry_delay, e)
            await asyncio.sleep(retry_delay)
