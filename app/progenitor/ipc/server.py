"""
IPC Server for Progenitor.

Provides a socket-based server that accepts connections from the chat_server
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

    Accepts connections from chat_server clients and routes messages
    to the Progenitor loop for processing.
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
        logger.info("Processing request %s: %s", request_id, content[:50])

        # Enqueue input and wait for output
        success = await self.loop_runner.enqueue_input(content)
        logger.info("Input enqueued for %s: success=%s, queue_size=%d",
                    request_id, success, self.loop_runner._input_queue.qsize())

        # Wait for the response (with timeout)
        try:
            response = await asyncio.wait_for(
                self._wait_for_response(request_id),
                timeout=300,  # 5 minute timeout
            )
            logger.info("Got response for %s: %d chars", request_id, len(response))
            await write_message(writer, assistant_message(response, request_id))
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
                logger.debug("Still waiting for response %s (poll %d, output_queue_size=%d)",
                            request_id, poll_count, self.loop_runner._output_queue.qsize())
            chunk = await self.loop_runner.get_output(timeout=1.0)
            if chunk:
                full_response += chunk
                logger.debug("Got chunk for %s: %d chars", request_id, len(chunk))
                # Check if this is the final response
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
        logger.info("Processing streaming request %s", request_id)

        # Enqueue input
        await self.loop_runner.enqueue_input(content)

        # Stream chunks from the loop runner
        try:
            chunks_sent = 0
            while True:
                # Try to get a stream chunk first
                chunk_data = await self.loop_runner.get_stream_chunk(timeout=0.5)

                if chunk_data:
                    content_chunk = chunk_data.get("content", "")
                    is_final = chunk_data.get("is_final", False)

                    if content_chunk:
                        await write_message(
                            writer,
                            stream_chunk(content_chunk, request_id, is_final),
                        )
                        chunks_sent += 1

                    if is_final:
                        await write_message(writer, stream_end(request_id))
                        break
                else:
                    # Check for complete output
                    output = await self.loop_runner.get_output(timeout=0.1)
                    if output:
                        # Send as final chunk
                        await write_message(
                            writer,
                            stream_chunk(output, request_id, is_final=True),
                        )
                        await write_message(writer, stream_end(request_id))
                        break

                # Timeout check (5 minutes)
                if chunks_sent == 0:
                    await asyncio.sleep(0.1)

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
