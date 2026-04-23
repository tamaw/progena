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
import contextlib
import inspect
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from .protocol import (
    IPCMessage,
    MessageType,
    agent_event,
    assistant_message,
    error_message,
    heartbeat_ack,
    read_message,
    ready_message,
    stream_chunk,
    stream_end,
    stream_event,
    write_message,
)

if TYPE_CHECKING:
    from ..runtime.loop import ProgenitorLoop

logger = logging.getLogger(__name__)

# Server defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9500
MAX_CLIENTS = 10
DEFAULT_CLIENT_READ_TIMEOUT_SECONDS = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_CYCLE_COMPLETE_TIMEOUT_SECONDS = 5.0
STREAM_POLL_INTERVAL_SECONDS = 0.1


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
        client_read_timeout_seconds: float = DEFAULT_CLIENT_READ_TIMEOUT_SECONDS,
        request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        cycle_complete_timeout_seconds: float = DEFAULT_CYCLE_COMPLETE_TIMEOUT_SECONDS,
    ):
        """Initialize the IPC server.

        Args:
            loop_runner: The Progenitor loop runner for message processing.
            host: Host to bind to.
            port: Port to bind to.
            max_clients: Maximum concurrent clients.
            client_read_timeout_seconds: Idle timeout while reading client messages.
            request_timeout_seconds: Max time a single-flight request may block the server.
            cycle_complete_timeout_seconds: Extra wait before allowing the next request.
        """
        self.loop_runner = loop_runner
        self.host = host
        self.port = port
        self.max_clients = max_clients
        self.client_read_timeout_seconds = client_read_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.cycle_complete_timeout_seconds = cycle_complete_timeout_seconds

        self._server: asyncio.Server | None = None
        self._running = False
        self._client_count = 0
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._shutdown_event = asyncio.Event()
        # The runtime loop exposes one shared output/stream channel and one shared
        # mutable state machine, so chat requests are intentionally single-flight.
        # Keep the waits bounded so one slow request does not stall every client
        # for multiple minutes.
        self._request_lock = asyncio.Lock()
        # Background task for broadcasting events
        self._event_broadcast_task: asyncio.Task | None = None

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

        if self._server is not None:
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
            with contextlib.suppress(asyncio.CancelledError):
                await self._event_broadcast_task
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
                    msg = await read_message(
                        reader,
                        timeout=self.client_read_timeout_seconds,
                    )
                except TimeoutError:
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
            logger.info(
                "Client %s disconnected, %d clients remaining", client_id, self._client_count
            )

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
                        msg.content, request_id, writer, msg.metadata
                    )
                else:
                    await self._handle_request(msg.content, request_id, writer, msg.metadata)

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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Handle a non-streaming request.

        Args:
            content: User message content.
            request_id: Request identifier.
            writer: Client stream writer.
        """
        await self._notify_queued_request(request_id, streaming=False)

        # Requests are serialized globally because the loop runner does not expose
        # per-request output routing yet.
        async with self._request_lock:
            logger.info("Processing request %s: %s", request_id, content[:50])

            await self._drain_response_queues(request_id)

            # Enqueue input and wait for output
            await self._enqueue_loop_input(content, metadata=metadata)

            # Wait for the response (with timeout)
            try:
                response = await self._wait_for_response(
                    request_id,
                    timeout=self.request_timeout_seconds,
                )
                logger.info("Got response for %s: %d chars", request_id, len(response))
                await write_message(writer, assistant_message(response, request_id))

                cycle_done = await self.loop_runner.wait_cycle_complete(
                    timeout=self.cycle_complete_timeout_seconds
                )
                if not cycle_done:
                    logger.warning("Cycle complete timeout for %s", request_id)

            except TimeoutError:
                logger.error("Request %s timed out waiting for response", request_id)
                await self._drain_response_queues(request_id)
                await write_message(
                    writer,
                    error_message("Request timed out", error_code="TIMEOUT", request_id=request_id),
                )

    async def _enqueue_loop_input(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Enqueue input on loop implementations with or without metadata support."""

        enqueue_input = self.loop_runner.enqueue_input
        parameters = inspect.signature(enqueue_input).parameters
        if "metadata" in parameters:
            await enqueue_input(content, metadata=metadata)
            return
        await enqueue_input(content)

    async def _wait_for_response(self, request_id: str, timeout: float | None = None) -> str:
        """Wait for a response from the loop runner.

        Collects stream chunks from the stream queue until is_final=True.
        """
        full_response = ""
        poll_count = 0
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                poll_timeout = min(STREAM_POLL_INTERVAL_SECONDS, remaining)
            else:
                poll_timeout = STREAM_POLL_INTERVAL_SECONDS

            poll_count += 1
            if poll_count % 50 == 0:
                logger.debug("Still waiting for %s (poll %d)", request_id, poll_count)
            chunk_data = await self._get_stream_item(timeout=poll_timeout)
            if not chunk_data:
                continue
            item_type = str(chunk_data.get("type", "stream_chunk"))
            if item_type == "stream_event":
                continue
            if item_type == "stream_end":
                return full_response
            full_response += str(chunk_data.get("content", ""))
            if chunk_data.get("is_final"):
                return full_response

    async def _handle_streaming_request(
        self,
        content: str,
        request_id: str,
        writer: asyncio.StreamWriter,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Handle a streaming request.

        Args:
            content: User message content.
            request_id: Request identifier.
            writer: Client stream writer.
        """
        await self._notify_queued_request(request_id, streaming=True)

        # Requests are serialized globally because the loop runner does not expose
        # per-request output routing yet.
        async with self._request_lock:
            logger.info("Processing streaming request %s: %s", request_id, content[:50])

            await self._drain_response_queues(request_id)

            # Enqueue input
            await self._enqueue_loop_input(content, metadata=metadata)

            # Stream chunks from the loop runner's stream queue
            try:
                deadline = time.monotonic() + self.request_timeout_seconds
                poll_count = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    poll_count += 1

                    # Get stream chunk from the stream queue
                    chunk_data = await self._get_stream_item(
                        timeout=min(STREAM_POLL_INTERVAL_SECONDS, remaining)
                    )

                    if chunk_data:
                        item_type = str(chunk_data.get("type", "stream_chunk"))
                        if item_type == "stream_event":
                            await write_message(
                                writer,
                                stream_event(
                                    event_type=str(chunk_data.get("event_type", "unknown")),
                                    request_id=request_id,
                                    content=str(chunk_data.get("content", "")),
                                    metadata=chunk_data.get("metadata"),
                                ),
                            )
                            continue

                        if item_type == "stream_end":
                            await write_message(writer, stream_end(request_id))
                            logger.info("Stream complete for %s", request_id)
                            cycle_done = await self.loop_runner.wait_cycle_complete(
                                timeout=self.cycle_complete_timeout_seconds
                            )
                            if not cycle_done:
                                logger.warning("Cycle complete timeout for %s", request_id)
                            return

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

                            cycle_done = await self.loop_runner.wait_cycle_complete(
                                timeout=self.cycle_complete_timeout_seconds
                            )
                            if not cycle_done:
                                logger.warning("Cycle complete timeout for %s", request_id)

                            return  # Success - exit the handler

                    # Brief yield to allow other tasks
                    if poll_count % 100 == 0:
                        logger.debug(
                            "Still waiting for stream chunks %s (poll %d)", request_id, poll_count
                        )

                # If we exited the loop without finding output, we timed out
                logger.error(
                    "Streaming request %s timed out after %.1fs",
                    request_id,
                    self.request_timeout_seconds,
                )
                await self._drain_response_queues(request_id)
                await write_message(
                    writer,
                    error_message(
                        "Streaming timed out", error_code="TIMEOUT", request_id=request_id
                    ),
                )

            except Exception as e:
                logger.error("Streaming error for %s: %s", request_id, e)
                await self._drain_response_queues(request_id)
                await write_message(
                    writer,
                    error_message(str(e), error_code="STREAM_ERROR", request_id=request_id),
                )

    async def _get_stream_item(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Read the next stream item from loop runners old and new."""
        get_stream_item = getattr(self.loop_runner, "get_stream_item", None)
        if callable(get_stream_item):
            result = get_stream_item(timeout=timeout)
            if inspect.isawaitable(result):
                return cast(dict[str, Any] | None, await result)
            return cast(dict[str, Any] | None, result)
        return await self.loop_runner.get_stream_chunk(timeout=timeout)

    async def _notify_queued_request(self, request_id: str, *, streaming: bool) -> None:
        """Emit a message_queued event when another request already holds the lock."""
        if not self._request_lock.locked():
            return

        logger.info(
            "%s request %s queued behind active request",
            "Streaming" if streaming else "Non-streaming",
            request_id,
        )
        await self.loop_runner._push_event(
            "message_queued",
            "A previous message is still being processed; this message will run next.",
            {"request_id": request_id, "streaming": streaming},
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
            "request_serialization": "global_single_flight",
            "client_read_timeout_seconds": self.client_read_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "cycle_complete_timeout_seconds": self.cycle_complete_timeout_seconds,
        }

    async def _drain_response_queues(self, request_id: str) -> None:
        """Clear any stale response data left behind by earlier requests."""
        drained_output = 0
        while not self.loop_runner._output_queue.empty():
            try:
                stale = self.loop_runner._output_queue.get_nowait()
                drained_output += 1
                logger.warning(
                    "Drained stale output (%d chars) for %s",
                    len(stale) if stale else 0,
                    request_id,
                )
            except Exception:
                break

        drained_stream = 0
        while not self.loop_runner._stream_queue.empty():
            try:
                self.loop_runner._stream_queue.get_nowait()
                drained_stream += 1
                logger.warning("Drained stale stream chunk for %s", request_id)
            except Exception:
                break

        if drained_output or drained_stream:
            logger.info(
                "Drained stale response data before/after %s (output=%d, stream=%d)",
                request_id,
                drained_output,
                drained_stream,
            )

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
                    logger.debug(
                        "No clients connected, dropping event: %s", event.get("event_type")
                    )
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
                        logger.debug(
                            "Sent event %s to client %s", event.get("event_type"), client_id
                        )
                    except Exception as e:
                        logger.warning("Failed to send event to client %s: %s", client_id, e)
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
