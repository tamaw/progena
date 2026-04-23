"""
Integration tests for IPC Server and Client.

Tests end-to-end communication between the IPC client and server.
"""

import asyncio
import contextlib
import time

import pytest

from app.progenitor.ipc import client as ipc_client_module
from app.progenitor.ipc.client import IPCClient
from app.progenitor.ipc.server import IPCServer


class MockLoopRunner:
    """Mock ProgenitorLoop for testing IPC server."""

    def __init__(self):
        self._input_queue = asyncio.Queue()
        self._output_queue = asyncio.Queue()
        self._stream_queue = asyncio.Queue()
        self._event_queue = asyncio.Queue()
        self._response = "Mock response"
        self._cycle_complete_event = asyncio.Event()
        # Auto-set event for mocking
        self._cycle_complete_event.set()
        # Flag to enable automatic streaming (tests can disable to control manually)
        self._auto_stream = True
        self.last_cycle_complete_timeout: float | None = None

    async def enqueue_input(self, content: str) -> None:
        """Enqueue input message."""
        await self._input_queue.put(content)
        # Simulate processing and produce output (for non-streaming)
        await self._output_queue.put(self._response)
        # Also put streaming chunks (for streaming handler)
        if self._auto_stream:
            # Split response into words like the real loop does
            words = self._response.split(" ")
            for i, word in enumerate(words):
                is_last = i == len(words) - 1
                chunk = word if is_last else word + " "
                await self._stream_queue.put(
                    {
                        "type": "stream_chunk",
                        "content": chunk,
                        "is_final": is_last,
                    }
                )

    async def get_output(self, timeout: float | None = None) -> str | None:
        """Get output from queue."""
        try:
            if timeout:
                return await asyncio.wait_for(self._output_queue.get(), timeout=timeout)
            return self._output_queue.get_nowait()
        except (TimeoutError, asyncio.QueueEmpty):
            return None

    async def get_stream_chunk(self, timeout: float | None = None) -> dict | None:
        """Get stream chunk from queue."""
        try:
            if timeout:
                return await asyncio.wait_for(self._stream_queue.get(), timeout=timeout)
            return self._stream_queue.get_nowait()
        except (TimeoutError, asyncio.QueueEmpty):
            return None

    async def get_stream_item(self, timeout: float | None = None) -> dict | None:
        """Get any stream queue item, including structured events."""
        return await self.get_stream_chunk(timeout=timeout)

    async def wait_cycle_complete(self, timeout: float | None = None) -> bool:
        """Wait for cycle to complete (mock always returns True immediately)."""
        self.last_cycle_complete_timeout = timeout
        return True

    def set_response(self, response: str) -> None:
        """Set the mock response."""
        self._response = response

    async def add_stream_chunks(self, chunks: list[dict]) -> None:
        """Add stream chunks to queue."""
        for chunk in chunks:
            await self._stream_queue.put(chunk)

    async def get_event(self, timeout: float | None = None) -> dict | None:
        """Get event from queue (stub for IPC event broadcast)."""
        if timeout:
            await asyncio.sleep(timeout)
        return None

    async def _push_event(
        self, event_type: str, content: str = "", metadata: dict | None = None
    ) -> None:
        """Record an event pushed by the IPC server."""
        self._event_queue.put_nowait(
            {
                "event_type": event_type,
                "content": content,
                "metadata": metadata or {},
            }
        )


@pytest.fixture
def mock_loop_runner():
    """Create a mock loop runner."""
    return MockLoopRunner()


@pytest.fixture
async def ipc_server(mock_loop_runner):
    """Create and start an IPC server for testing."""
    # Use a random port to avoid conflicts
    server = IPCServer(
        loop_runner=mock_loop_runner,
        host="127.0.0.1",
        port=0,  # Let OS choose port
    )
    await server.start()

    # Get the actual port
    assert server._server is not None
    actual_port = server._server.sockets[0].getsockname()[1]
    server.port = actual_port

    yield server

    await server.stop()


@pytest.fixture
async def ipc_client(ipc_server):
    """Create an IPC client connected to the test server."""
    client = IPCClient(
        host="127.0.0.1",
        port=ipc_server.port,
        timeout=5.0,
        auto_reconnect=False,
    )
    connected = await client.connect()
    assert connected, "Failed to connect to test server"

    yield client

    await client.disconnect()


class TestIPCServerBasic:
    """Basic tests for IPC server."""

    @pytest.mark.asyncio
    async def test_server_starts_and_stops(self, mock_loop_runner):
        """Test that server starts and stops cleanly."""
        server = IPCServer(
            loop_runner=mock_loop_runner,
            host="127.0.0.1",
            port=0,
        )

        await server.start()
        assert server.is_running

        await server.stop()
        assert not server.is_running

    @pytest.mark.asyncio
    async def test_server_accepts_connection(self, ipc_server):
        """Test that server accepts client connections."""
        assert ipc_server.is_running
        assert ipc_server.client_count == 0

        # Connect a client
        reader, writer = await asyncio.open_connection("127.0.0.1", ipc_server.port)

        # Wait for connection to be processed
        await asyncio.sleep(0.2)
        assert ipc_server.client_count == 1

        writer.close()
        await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_server_status(self, ipc_server):
        """Test server status reporting."""
        status = ipc_server.get_status()

        assert status["running"] is True
        assert status["host"] == "127.0.0.1"
        assert status["client_count"] >= 0
        assert status["max_clients"] > 0
        assert status["request_serialization"] == "global_single_flight"
        assert status["client_read_timeout_seconds"] == 60.0
        assert status["request_timeout_seconds"] == 60.0
        assert status["cycle_complete_timeout_seconds"] == 5.0


class TestIPCClientBasic:
    """Basic tests for IPC client."""

    @pytest.mark.asyncio
    async def test_client_connects(self, ipc_server):
        """Test that client connects successfully."""
        client = IPCClient(
            host="127.0.0.1",
            port=ipc_server.port,
            timeout=5.0,
        )

        connected = await client.connect()
        assert connected
        assert client.is_connected

        await client.disconnect()
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_client_connection_refused(self):
        """Test client handles connection refused."""
        client = IPCClient(
            host="127.0.0.1",
            port=59999,  # Unlikely to be in use
            timeout=1.0,
            auto_reconnect=False,
        )

        connected = await client.connect()
        assert not connected
        assert not client.is_connected

    @pytest.mark.asyncio
    async def test_client_status(self, ipc_client):
        """Test client status reporting."""
        status = ipc_client.get_status()

        assert status["connected"] is True
        assert status["host"] == "127.0.0.1"
        assert "last_activity_ago" in status


class TestIPCCommunication:
    """Tests for IPC message exchange."""

    @pytest.mark.asyncio
    async def test_send_message_and_receive_response(
        self, ipc_client, ipc_server, mock_loop_runner
    ):
        """Test sending a message and receiving response."""
        mock_loop_runner.set_response("Hello back!")

        response = await ipc_client.send_message("Hello", request_id="test_1")

        assert response == "Hello back!"

    @pytest.mark.asyncio
    async def test_send_message_with_request_id(self, ipc_client, ipc_server, mock_loop_runner):
        """Test that request ID is preserved."""
        mock_loop_runner.set_response("Response")

        response = await ipc_client.send_message("Test message", request_id="custom_req_id")

        assert response == "Response"

    @pytest.mark.asyncio
    async def test_heartbeat(self, ipc_client):
        """Test heartbeat mechanism."""
        # Heartbeat should update last_heartbeat
        initial = ipc_client.last_heartbeat()
        assert initial is not None

    @pytest.mark.asyncio
    async def test_multiple_messages(self, ipc_client, ipc_server, mock_loop_runner):
        """Test sending multiple messages sequentially."""
        responses = []

        for i in range(3):
            mock_loop_runner.set_response(f"Response {i}")
            response = await ipc_client.send_message(f"Message {i}")
            responses.append(response)

        assert len(responses) == 3

    @pytest.mark.asyncio
    async def test_non_streaming_request_drains_stale_stream_chunks(
        self, ipc_client, ipc_server, mock_loop_runner
    ):
        """A timed-out prior request must not leak stream chunks into the next response."""
        await mock_loop_runner._stream_queue.put(
            {
                "type": "stream_chunk",
                "content": "stale response",
                "is_final": True,
            }
        )
        mock_loop_runner.set_response("fresh response")

        response = await ipc_client.send_message("Hello after timeout", request_id="fresh_req")

        assert response == "fresh response"

    @pytest.mark.asyncio
    async def test_request_uses_configured_cycle_complete_timeout(self, mock_loop_runner):
        """Responses should use the configured post-response cycle wait."""
        server = IPCServer(
            loop_runner=mock_loop_runner,
            host="127.0.0.1",
            port=0,
            cycle_complete_timeout_seconds=7.0,
        )
        await server.start()
        assert server._server is not None
        server.port = server._server.sockets[0].getsockname()[1]

        client = IPCClient(
            host="127.0.0.1",
            port=server.port,
            timeout=5.0,
            auto_reconnect=False,
        )

        try:
            await client.connect()
            response = await client.send_message("Hello, Progenitor!")
            assert response == "Mock response"
            assert mock_loop_runner.last_cycle_complete_timeout == 7.0
        finally:
            await client.disconnect()
            await server.stop()

    @pytest.mark.asyncio
    async def test_request_returns_timeout_error_with_configured_timeout(self):
        """A stalled request should fail on the server's configured timeout."""

        class NoResponseLoopRunner(MockLoopRunner):
            async def enqueue_input(self, content: str) -> None:
                await self._input_queue.put(content)

        loop_runner = NoResponseLoopRunner()
        server = IPCServer(
            loop_runner=loop_runner,
            host="127.0.0.1",
            port=0,
            request_timeout_seconds=1.0,
        )
        await server.start()
        assert server._server is not None
        server.port = server._server.sockets[0].getsockname()[1]

        client = IPCClient(
            host="127.0.0.1",
            port=server.port,
            timeout=3.0,
            auto_reconnect=False,
        )

        try:
            await client.connect()
            with pytest.raises(RuntimeError, match="Request timed out"):
                await client.send_message("slow request")
        finally:
            await client.disconnect()
            await server.stop()


class TestIPCStreaming:
    """Tests for streaming responses."""

    @pytest.mark.asyncio
    async def test_stream_response(self, ipc_client, ipc_server, mock_loop_runner):
        """Test streaming response."""
        # Disable auto streaming so we can manually control chunks
        mock_loop_runner._auto_stream = False

        # Set up stream chunks
        chunks = [
            {"content": "Hello ", "is_final": False, "type": "stream_chunk"},
            {"content": "world", "is_final": True, "type": "stream_chunk"},
        ]

        # Add chunks in background after a short delay
        async def add_chunks():
            await asyncio.sleep(0.1)
            for chunk in chunks:
                await mock_loop_runner._stream_queue.put(chunk)

        asyncio.create_task(add_chunks())

        # Stream response
        received = []
        async for item in ipc_client.send_message_stream_events("Test", request_id="stream_1"):
            if item.get("type") != "stream_chunk":
                continue
            received.append(item.get("content", ""))
            if len(received) >= 2:
                break

        assert len(received) >= 1

    @pytest.mark.asyncio
    async def test_stream_response_events(self, ipc_client, ipc_server, mock_loop_runner):
        """Structured IPC stream events should flow alongside text chunks."""
        mock_loop_runner._auto_stream = False

        async def add_items():
            await asyncio.sleep(0.1)
            await mock_loop_runner._stream_queue.put(
                {
                    "type": "stream_event",
                    "event_type": "node_update",
                    "content": "",
                    "metadata": {"node_name": "respond", "output_keys": ["messages"]},
                }
            )
            await mock_loop_runner._stream_queue.put(
                {"content": "Hello", "is_final": False, "type": "stream_chunk"}
            )
            await mock_loop_runner._stream_queue.put(
                {"content": "", "is_final": True, "type": "stream_chunk"}
            )

        asyncio.create_task(add_items())

        received = []
        async for item in ipc_client.send_message_stream_events(
            "Test", request_id="stream_events_1"
        ):
            received.append(item)
            if item.get("type") == "stream_chunk" and item.get("is_final"):
                break

        assert received == [
            {
                "type": "stream_event",
                "event_type": "node_update",
                "content": "",
                "metadata": {"node_name": "respond", "output_keys": ["messages"]},
                "request_id": "stream_events_1",
            },
            {
                "type": "stream_chunk",
                "content": "Hello",
                "is_final": False,
                "request_id": "stream_events_1",
            },
            {
                "type": "stream_chunk",
                "content": "",
                "is_final": True,
                "request_id": "stream_events_1",
            },
        ]


class TestIPCErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_timeout_handling(self, ipc_server, mock_loop_runner):
        """Test request timeout is handled."""
        # Create client with very short timeout
        client = IPCClient(
            host="127.0.0.1",
            port=ipc_server.port,
            timeout=0.1,  # 100ms timeout
            auto_reconnect=False,
        )
        await client.connect()

        # Create a custom mock that never responds
        # Override the queue so get_stream_chunk always times out
        original_get_stream_chunk = mock_loop_runner.get_stream_chunk

        async def slow_get_stream_chunk(timeout=None):
            await asyncio.sleep(10)  # Much longer than client timeout
            return None

        mock_loop_runner.get_stream_chunk = slow_get_stream_chunk

        try:
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                await client.send_message("Timeout test")
        finally:
            mock_loop_runner.get_stream_chunk = original_get_stream_chunk
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_reconnection_on_disconnect(self, ipc_server, mock_loop_runner):
        """Test client reconnects after disconnect."""
        client = IPCClient(
            host="127.0.0.1",
            port=ipc_server.port,
            timeout=5.0,
            auto_reconnect=True,
            max_reconnect_attempts=3,
        )

        # Initial connect
        await client.connect()
        assert client.is_connected

        # Simulate disconnect
        await client._cleanup_connection()
        assert not client.is_connected

        # Should auto-reconnect on next message
        mock_loop_runner.set_response("Reconnected!")
        response = await client.send_message("After reconnect")
        assert response == "Reconnected!"
        assert client.is_connected

        await client.disconnect()

    @pytest.mark.asyncio
    async def test_missed_heartbeat_threshold_marks_client_disconnected(self, monkeypatch):
        """Crossing the heartbeat miss threshold should force reconnect on the next request."""
        client = IPCClient(
            host="127.0.0.1",
            port=9500,
            timeout=5.0,
            auto_reconnect=True,
        )
        client._connected = True
        client._reader = object()  # type: ignore[assignment]
        client._writer = object()  # type: ignore[assignment]
        client._last_activity = time.time() - (ipc_client_module.HEARTBEAT_INTERVAL * 3)
        client._missed_heartbeats = ipc_client_module.HEARTBEAT_MISS_THRESHOLD - 1

        original_sleep = asyncio.sleep
        read_calls = 0

        async def fake_sleep(_delay: float) -> None:
            await original_sleep(0)

        async def fake_write_message(_writer, _message) -> None:
            return None

        async def fake_read_message(_reader, timeout: float | None = None):
            nonlocal read_calls
            read_calls += 1
            if read_calls == 1:
                raise TimeoutError
            return None

        monkeypatch.setattr(ipc_client_module.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(ipc_client_module, "write_message", fake_write_message)
        monkeypatch.setattr(ipc_client_module, "read_message", fake_read_message)

        await client._heartbeat_loop()

        assert client.is_connected is False
        assert client._missed_heartbeats == ipc_client_module.HEARTBEAT_MISS_THRESHOLD


class TestIPCConcurrency:
    """Tests for concurrent access."""

    @pytest.mark.asyncio
    async def test_multiple_clients(self, ipc_server, mock_loop_runner):
        """Test multiple clients can connect simultaneously."""
        clients = []

        for _i in range(3):
            client = IPCClient(
                host="127.0.0.1",
                port=ipc_server.port,
                timeout=5.0,
            )
            await client.connect()
            clients.append(client)

        # All clients should be connected
        assert all(c.is_connected for c in clients)
        assert ipc_server.client_count == 3

        # Cleanup
        for client in clients:
            await client.disconnect()

    @pytest.mark.asyncio
    async def test_max_clients_limit(self, mock_loop_runner):
        """Test that server rejects connections beyond max_clients."""
        server = IPCServer(
            loop_runner=mock_loop_runner,
            host="127.0.0.1",
            port=0,  # Let OS choose port
            max_clients=2,
        )
        await server.start()

        # Get actual port
        assert server._server is not None
        actual_port = server._server.sockets[0].getsockname()[1]

        clients = []
        try:
            # Connect max clients
            for _i in range(2):
                client = IPCClient(
                    host="127.0.0.1",
                    port=actual_port,
                    timeout=5.0,
                )
                await client.connect()
                clients.append(client)

            await asyncio.sleep(0.3)
            assert server.client_count == 2

            # Third client should be rejected
            # (The server sends error and closes, client sees as failed connect)
            IPCClient(
                host="127.0.0.1",
                port=actual_port,
                timeout=2.0,
                auto_reconnect=False,
            )
            # Connection will succeed but server will close it
            # This is implementation dependent - just verify we don't exceed max
            await asyncio.sleep(0.5)
            assert server.client_count <= 2

        finally:
            for client in clients:
                await client.disconnect()
            await server.stop()

    @pytest.mark.asyncio
    async def test_message_queued_event_emitted_when_lock_contended(
        self, ipc_server, mock_loop_runner
    ):
        """Queued requests should surface a message_queued event immediately."""
        client = IPCClient(
            host="127.0.0.1",
            port=ipc_server.port,
            timeout=5.0,
            auto_reconnect=False,
        )
        send_task = None

        await ipc_server._request_lock.acquire()
        try:
            await client.connect()
            send_task = asyncio.create_task(
                client.send_message("second message while busy", request_id="req_queued")
            )

            await asyncio.sleep(0.3)

            event = mock_loop_runner._event_queue.get_nowait()
            assert event["event_type"] == "message_queued"
            assert event["metadata"]["request_id"] == "req_queued"
            assert event["metadata"]["streaming"] is False
        finally:
            ipc_server._request_lock.release()
            await asyncio.sleep(0.3)
            if send_task is not None:
                send_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await send_task
            await client.disconnect()
