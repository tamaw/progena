"""
IPC Server for Progenitor.

Provides a socket-based server that accepts connections from the backend
and handles message processing through the Progenitor loop.

Features:
- TCP socket server (localhost by default)
- Concurrent client handling
- Request routing to Progenitor loop
- Streaming response support
- Graceful shutdown
"""

import asyncio
import logging
import uuid
from typing import Any, Callable, Optional, TYPE_CHECKING

from .protocol import (
    MessageType,
    IPCMessage,
    read_message,
    write_message,
    assistant_message,
    stream_chunk,
    stream_end,
    error_message,
    heartbeat_ack,
    ready_message,
    agent_event,
)

if TYPE_CHECKING:
    from ..runtime.loop import ProgenitorLoop

logger = logging.getLogger(__name__)

# Server defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9500
MAX_CLIENTS = 10


class IPCServer:
    """Socket-based IPC server for Progenitor.

    Accepts connections from backend clients and routes messages
    to the Progenitor loop for processing.

    Also broadcasts background events from the Progenitor loop to all
    connected clients, enabling real-time updates about backlog/schedule work.
    """

    def __init__(
        self,
        loop_runner: "ProgenitorLoop",
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        max_clients: int = MAX_CLIENTS,
    ):
        """Initialize the IPC server.

        Args:
            loop_runner: The Progenitor loop runner for message processing.
            host: Host to bind to.
            port: Port to bind to.
            max_clients: Maximum concurrent clients.
        """
        self.loop_runner = loop_runner
        self.host = host
        self.port = port
        self.max_clients = max_clients

        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._client_count = 0
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._shutdown_event = asyncio.Event()
        # Serialize requests to prevent output queue race conditions
        # Only one request can be processed at a time since the loop
        # doesn't track request IDs through the output queue
        self._request_lock = asyncio.Lock()
        # Background task for broadcasting events
        self._event_broadcast_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the IPC server."""
        if self._running:
            return

        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            reuse_address=True,
        )

        self._running = True
        self._shutdown_event.clear()

        # Start event broadcast task
        self._event_broadcast_task = asyncio.create_task(self._event_broadcast_loop())
        logger.info("Event broadcast task started")

        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        logger.info("IPC server listening on %s", addrs)

    async def serve_forever(self) -> None:
        """Run the server until shutdown is requested."""
        if not self._server:
            await self.start()

        async with self._server:
            await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the IPC server."""
        if not self._running:
            return

        logger.info("Stopping IPC server...")
        self._running = False
        self._shutdown_event.set()

        # Stop event broadcast task
        if self._event_broadcast_task and not self._event_broadcast_task.done():
            self._event_broadcast_task.cancel()
            try:
                await self._event_broadcast_task
            except asyncio.CancelledError:
                pass
            self._event_broadcast_task = None

        # Close all client connections
        for client_id, writer in list(self._clients.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug("Error closing client %s: %s", client_id, e)

        self._clients.clear()

        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("IPC server stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a client connection.

        Args:
            reader: Client stream reader.
            writer: Client stream writer.
        """
        client_id = f"client_{uuid.uuid4().hex[:8]}"
        peer = writer.get_extra_info("peername")
        logger.info("Client connected: %s from %s", client_id, peer)

        if self._client_count >= self.max_clients:
            logger.warning("Max clients reached, rejecting %s", client_id)
            await write_message(
                writer,
                error_message("Server at capacity", error_code="MAX_CLIENTS"),
            )
            writer.close()
            await writer.wait_closed()
            return

        self._clients[client_id] = writer
        self._client_count += 1

        try:
            # Send ready message
            await write_message(writer, ready_message())

            # Process messages
            while self._running:
                try:
                    msg = await read_message(reader, timeout=300)  # 5 min timeout
                except asyncio.TimeoutError:
                    logger.debug("Client %s timed out", client_id)
                    break

                if msg is None:
                    logger.info("Client %s disconnected", client_id)
                    break

                await self._process_message(client_id, msg, writer)

        except Exception as e:
            logger.error("Error handling client %s: %s", client_id, e, exc_info=True)
        finally:
            self._clients.pop(client_id, None)
            self._client_count -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug("Error closing client writer (expected during shutdown): %s", e)
            logger.info("Client %s disconnected, %d clients remaining",
                        client_id, self._client_count)

    async def _process_message(
        self,
        client_id: str,
        msg: IPCMessage,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process a client message.

        Args:
            client_id: Client identifier.
            msg: Received message.
            writer: Client stream writer.
        """
        logger.debug("Processing message from %s: type=%s", client_id, msg.type)

        try:
            if msg.type == MessageType.HEARTBEAT:
                await write_message(writer, heartbeat_ack())

            elif msg.type == MessageType.SHUTDOWN:
                logger.info("Shutdown requested by %s", client_id)
                self._shutdown_event.set()

            elif msg.type == MessageType.USER_MESSAGE:
                request_id = msg.request_id or f"req_{uuid.uuid4().hex[:12]}"

                if msg.stream:
                    await self._handle_streaming_request(
                        msg.content, request_id, writer
                    )
                else:
                    await self._handle_request(
                        msg.content, request_id, writer
                    )

            else:
                logger.warning("Unknown message type: %s", msg.type)
                await write_message(
                    writer,
                    error_message(
                        f"Unknown message type: {msg.type}",
                        error_code="UNKNOWN_TYPE",
                        request_id=msg.request_id,
                    ),
                )

        except Exception as e:
            logger.error("Error processing message: %s", e, exc_info=True)
            await write_message(
                writer,
                error_message(
                    str(e),
                    error_code="PROCESSING_ERROR",
                    request_id=msg.request_id,
                ),
            )

    async def _handle_request(
        self,
        content: str,
        request_id: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a non-streaming request.

        Args:
            content: User message content.
            request_id: Request identifier.
            writer: Client stream writer.
        """
        # Serialize requests to prevent output queue race conditions.
        # The loop doesn't track request IDs, so we must ensure only
        # one request is processed at a time.
        async with self._request_lock:
            logger.info("Processing request %s: %s", request_id, content[:50])

            # Drain any stale output from previous requests that may have timed out
            while not self.loop_runner._output_queue.empty():
                try:
                    stale = self.loop_runner._output_queue.get_nowait()
                    logger.warning("Drained stale output (%d chars) for %s",
                                   len(stale) if stale else 0, request_id)
                except Exception:
                    break

            # Enqueue input and wait for output
            await self.loop_runner.enqueue_input(content)

            # Wait for the response (with timeout)
            try:
                response = await asyncio.wait_for(
                    self._wait_for_response(request_id),
                    timeout=300,  # 5 minute timeout
                )
                logger.info("Got response for %s: %d chars", request_id, len(response))
                await write_message(writer, assistant_message(response, request_id))

                # CRITICAL: Wait for the graph cycle to fully complete
                # before releasing the lock.
                cycle_done = await self.loop_runner.wait_cycle_complete(timeout=30.0)
                if not cycle_done:
                    logger.warning("Cycle complete timeout for %s", request_id)

            except asyncio.TimeoutError:
                logger.error("Request %s timed out waiting for response", request_id)
                await write_message(
                    writer,
                    error_message("Request timed out", error_code="TIMEOUT", request_id=request_id),
                )

    async def _wait_for_response(self, request_id: str) -> str:
        """Wait for a response from the loop runner.

        This collects output from the loop runner's output queue.
        """
        # Poll the output queue
        full_response = ""
        poll_count = 0
        while True:
            poll_count += 1
            if poll_count % 10 == 0:
                logger.debug("Still waiting for %s (poll %d)", request_id, poll_count)
            chunk = await self.loop_runner.get_output(timeout=1.0)
            if chunk:
                full_response += chunk
                # The loop runner sends complete messages
                return full_response
            await asyncio.sleep(0.1)

    async def _handle_streaming_request(
        self,
        content: str,
        request_id: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a streaming request.

        Args:
            content: User message content.
            request_id: Request identifier.
            writer: Client stream writer.
        """
        # Serialize requests to prevent output queue race conditions.
        # The loop doesn't track request IDs, so we must ensure only
        # one request is processed at a time.
        async with self._request_lock:
            logger.info("Processing streaming request %s: %s", request_id, content[:50])

            # Drain any stale output/stream data from previous requests
            drained_count = 0
            while not self.loop_runner._output_queue.empty():
                try:
                    stale = self.loop_runner._output_queue.get_nowait()
                    drained_count += 1
                    logger.warning("Drained stale output (%d chars) for %s",
                                   len(stale) if stale else 0, request_id)
                except Exception:
                    break
            while not self.loop_runner._stream_queue.empty():
                try:
                    self.loop_runner._stream_queue.get_nowait()
                    logger.warning("Drained stale stream chunk for %s", request_id)
                except Exception:
                    break

            if drained_count > 0:
                logger.info("Drained %d stale items before processing %s", drained_count, request_id)

            # Enqueue input
            await self.loop_runner.enqueue_input(content)

            # Stream chunks from the loop runner's stream queue
            try:
                poll_count = 0
                max_polls = 3000  # 5 minutes with 0.1s sleep

                while poll_count < max_polls:
                    poll_count += 1

                    # Get stream chunk from the stream queue
                    chunk_data = await self.loop_runner.get_stream_chunk(timeout=0.1)

                    if chunk_data:
                        chunk_content = chunk_data.get("content", "")
                        is_final = chunk_data.get("is_final", False)

                        # Send chunk to client (including final empty chunk)
                        if chunk_content or is_final:
                            await write_message(
                                writer,
                                stream_chunk(chunk_content, request_id, is_final=is_final),
                            )

                        if is_final:
                            logger.info("Stream complete for %s", request_id)

                            # CRITICAL: Wait for the graph cycle to fully complete
                            # before releasing the lock. This prevents the next request
                            # from being enqueued before the progenitor is ready.
                            cycle_done = await self.loop_runner.wait_cycle_complete(timeout=30.0)
                            if not cycle_done:
                                logger.warning("Cycle complete timeout for %s", request_id)

                            return  # Success - exit the handler

                    # Brief yield to allow other tasks
                    if poll_count % 100 == 0:
                        logger.debug("Still waiting for stream chunks %s (poll %d)", request_id, poll_count)

                # If we exited the loop without finding output, we timed out
                logger.error("Streaming request %s timed out after %d polls", request_id, poll_count)
                await write_message(
                    writer,
                    error_message("Streaming timed out", error_code="TIMEOUT", request_id=request_id),
                )

            except Exception as e:
                logger.error("Streaming error for %s: %s", request_id, e)
                await write_message(
                    writer,
                    error_message(str(e), error_code="STREAM_ERROR", request_id=request_id),
                )

    @property
    def is_running(self) -> bool:
        """Check if the server is running."""
        return self._running

    @property
    def client_count(self) -> int:
        """Get the current client count."""
        return self._client_count

    def get_status(self) -> dict[str, Any]:
        """Get server status information."""
        return {
            "running": self._running,
            "host": self.host,
            "port": self.port,
            "client_count": self._client_count,
            "max_clients": self.max_clients,
        }

    async def _event_broadcast_loop(self) -> None:
        """Background task that broadcasts events from the loop to all connected clients.

        This enables real-time updates about background work (backlog, scheduled tasks)
        to be pushed to clients without requiring them to poll.
        """
        logger.info("Event broadcast loop started")

        while self._running:
            try:
                # Wait for an event from the loop runner
                event = await self.loop_runner.get_event(timeout=1.0)

                if event is None:
                    continue

                # Broadcast to all connected clients
                if not self._clients:
                    logger.debug("No clients connected, dropping event: %s", event.get("event_type"))
                    continue

                # Create the IPC message
                msg = agent_event(
                    event_type=event.get("event_type", "unknown"),
                    content=event.get("content", ""),
                    metadata=event.get("metadata"),
                )

                # Send to all clients (non-blocking, best effort)
                disconnected = []
                for client_id, writer in list(self._clients.items()):
                    try:
                        await write_message(writer, msg)
                        logger.debug("Sent event %s to client %s",
                                    event.get("event_type"), client_id)
                    except Exception as e:
                        logger.warning("Failed to send event to client %s: %s",
                                      client_id, e)
                        disconnected.append(client_id)

                # Clean up disconnected clients
                for client_id in disconnected:
                    self._clients.pop(client_id, None)
                    self._client_count = max(0, self._client_count - 1)

            except asyncio.CancelledError:
                logger.info("Event broadcast loop cancelled")
                break
            except Exception as e:
                logger.error("Event broadcast error: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

        logger.info("Event broadcast loop stopped")
