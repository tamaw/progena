"""
Tests for IPC (Inter-Process Communication) module.

Tests the socket-based communication protocol between backend and Progenitor.
"""

import asyncio
import json
import struct

import pytest

from app.progenitor.ipc.protocol import (
    HEADER_SIZE,
    MAX_MESSAGE_SIZE,
    IPCMessage,
    MessageType,
    assistant_message,
    decode_message,
    encode_message,
    error_message,
    heartbeat,
    heartbeat_ack,
    read_message,
    ready_message,
    shutdown_message,
    stream_chunk,
    stream_end,
    stream_event,
    user_message,
    write_message,
)


class TestIPCMessage:
    """Tests for IPCMessage dataclass."""

    def test_create_basic_message(self):
        """Test creating a basic message."""
        msg = IPCMessage(type=MessageType.USER_MESSAGE, content="Hello")
        assert msg.type == MessageType.USER_MESSAGE
        assert msg.content == "Hello"
        assert msg.request_id is None
        assert msg.is_final is False

    def test_to_dict_minimal(self):
        """Test converting message to dict with minimal fields."""
        msg = IPCMessage(type=MessageType.USER_MESSAGE, content="Hello")
        d = msg.to_dict()
        assert d["type"] == "user_message"
        assert d["content"] == "Hello"
        assert "request_id" not in d
        assert "is_final" not in d

    def test_to_dict_full(self):
        """Test converting message to dict with all fields."""
        msg = IPCMessage(
            type=MessageType.STREAM_CHUNK,
            content="chunk",
            request_id="req_123",
            is_final=True,
            stream=True,
            metadata={"key": "value"},
        )
        d = msg.to_dict()
        assert d["type"] == "stream_chunk"
        assert d["content"] == "chunk"
        assert d["request_id"] == "req_123"
        assert d["is_final"] is True
        assert d["stream"] is True
        assert d["metadata"] == {"key": "value"}

    def test_from_dict_basic(self):
        """Test creating message from dict."""
        d = {"type": "user_message", "content": "Hello"}
        msg = IPCMessage.from_dict(d)
        assert msg.type == MessageType.USER_MESSAGE
        assert msg.content == "Hello"

    def test_from_dict_full(self):
        """Test creating message from dict with all fields."""
        d = {
            "type": "stream_chunk",
            "content": "chunk",
            "request_id": "req_123",
            "is_final": True,
            "stream": True,
            "metadata": {"key": "value"},
        }
        msg = IPCMessage.from_dict(d)
        assert msg.type == MessageType.STREAM_CHUNK
        assert msg.content == "chunk"
        assert msg.request_id == "req_123"
        assert msg.is_final is True
        assert msg.metadata == {"key": "value"}

    def test_from_dict_unknown_type(self):
        """Test handling unknown message type."""
        d = {"type": "unknown_type", "content": "test"}
        msg = IPCMessage.from_dict(d)
        assert msg.type == "unknown_type"  # Kept as string
        assert msg.content == "test"


class TestProtocolEncoding:
    """Tests for message encoding/decoding."""

    def test_encode_message_ipc(self):
        """Test encoding an IPCMessage."""
        msg = IPCMessage(type=MessageType.USER_MESSAGE, content="Hello")
        data = encode_message(msg)

        # Check header
        assert len(data) >= HEADER_SIZE
        (length,) = struct.unpack(">I", data[:HEADER_SIZE])
        assert length == len(data) - HEADER_SIZE

        # Check payload
        payload = json.loads(data[HEADER_SIZE:].decode())
        assert payload["type"] == "user_message"
        assert payload["content"] == "Hello"

    def test_encode_message_dict(self):
        """Test encoding a dict."""
        d = {"type": "user_message", "content": "Hello"}
        data = encode_message(d)

        (length,) = struct.unpack(">I", data[:HEADER_SIZE])
        payload = json.loads(data[HEADER_SIZE:].decode())
        assert payload == d

    def test_decode_message(self):
        """Test decoding a message."""
        payload = json.dumps({"type": "user_message", "content": "Hello"}).encode()
        msg = decode_message(payload)

        assert msg.type == MessageType.USER_MESSAGE
        assert msg.content == "Hello"

    def test_encode_decode_roundtrip(self):
        """Test encoding and decoding round-trip."""
        original = IPCMessage(
            type=MessageType.STREAM_CHUNK,
            content="chunk data",
            request_id="req_abc",
            is_final=True,
        )
        data = encode_message(original)

        # Decode
        payload = data[HEADER_SIZE:]
        decoded = decode_message(payload)

        assert decoded.type == original.type
        assert decoded.content == original.content
        assert decoded.request_id == original.request_id
        assert decoded.is_final == original.is_final

    def test_encode_large_message_rejected(self):
        """Test that messages exceeding max size are rejected."""
        large_content = "x" * (MAX_MESSAGE_SIZE + 1000)
        msg = IPCMessage(type=MessageType.USER_MESSAGE, content=large_content)

        with pytest.raises(ValueError, match="exceeds maximum"):
            encode_message(msg)


class TestConvenienceFunctions:
    """Tests for convenience message creation functions."""

    def test_user_message(self):
        """Test user_message convenience function."""
        msg = user_message("Hello", request_id="req_1", stream=True)
        assert msg.type == MessageType.USER_MESSAGE
        assert msg.content == "Hello"
        assert msg.request_id == "req_1"
        assert msg.stream is True

    def test_assistant_message(self):
        """Test assistant_message convenience function."""
        msg = assistant_message("Response", request_id="req_1")
        assert msg.type == MessageType.ASSISTANT_MESSAGE
        assert msg.content == "Response"
        assert msg.is_final is True

    def test_stream_chunk(self):
        """Test stream_chunk convenience function."""
        msg = stream_chunk("chunk", request_id="req_1", is_final=False)
        assert msg.type == MessageType.STREAM_CHUNK
        assert msg.content == "chunk"
        assert msg.is_final is False

    def test_stream_end(self):
        """Test stream_end convenience function."""
        msg = stream_end(request_id="req_1")
        assert msg.type == MessageType.STREAM_END
        assert msg.is_final is True

    def test_stream_event(self):
        """Test stream_event convenience function."""
        msg = stream_event(
            "message_delta",
            request_id="req_1",
            content="chunk",
            metadata={"langgraph_node": "respond"},
        )
        assert msg.type == MessageType.STREAM_EVENT
        assert msg.event_type == "message_delta"
        assert msg.request_id == "req_1"
        assert msg.content == "chunk"
        assert msg.metadata == {"langgraph_node": "respond"}

    def test_error_message(self):
        """Test error_message convenience function."""
        msg = error_message("Error occurred", error_code="E001", request_id="req_1")
        assert msg.type == MessageType.ERROR
        assert msg.content == "Error occurred"
        assert msg.error_code == "E001"

    def test_heartbeat(self):
        """Test heartbeat convenience function."""
        msg = heartbeat()
        assert msg.type == MessageType.HEARTBEAT

    def test_heartbeat_ack(self):
        """Test heartbeat_ack convenience function."""
        msg = heartbeat_ack()
        assert msg.type == MessageType.HEARTBEAT_ACK

    def test_ready_message(self):
        """Test ready_message convenience function."""
        msg = ready_message()
        assert msg.type == MessageType.READY

    def test_shutdown_message(self):
        """Test shutdown_message convenience function."""
        msg = shutdown_message()
        assert msg.type == MessageType.SHUTDOWN


class TestAsyncProtocol:
    """Tests for async read/write functions."""

    @pytest.mark.asyncio
    async def test_write_and_read_message(self):
        """Test writing and reading a message through a stream."""
        # Create a pipe-like pair of streams
        reader, writer = await self._create_stream_pair()

        try:
            # Write message
            msg = user_message("Hello", request_id="req_123")
            await write_message(writer, msg)

            # Read message
            received = await read_message(reader, timeout=5.0)

            assert received is not None
            assert received.type == MessageType.USER_MESSAGE
            assert received.content == "Hello"
            assert received.request_id == "req_123"
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_read_message_timeout(self):
        """Test read timeout."""
        reader, writer = await self._create_stream_pair()

        try:
            # Try to read with short timeout (no data written)
            with pytest.raises(asyncio.TimeoutError):
                await read_message(reader, timeout=0.1)
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_read_message_connection_closed(self):
        """Test reading when connection is closed."""
        reader, writer = await self._create_stream_pair()

        # Close writer immediately
        writer.close()
        await writer.wait_closed()

        # Read should return None
        result = await read_message(reader, timeout=1.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_message_rejects_zero_length_frame(self):
        """Zero-length frames should be rejected before payload decode."""
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", 0))
        reader.feed_eof()

        with pytest.raises(ValueError, match="length 0"):
            await read_message(reader, timeout=1.0)

    @pytest.mark.asyncio
    async def test_multiple_messages(self):
        """Test sending and receiving multiple messages."""
        reader, writer = await self._create_stream_pair()

        try:
            messages = [
                user_message("First"),
                user_message("Second"),
                user_message("Third"),
            ]

            # Write all messages
            for msg in messages:
                await write_message(writer, msg)

            # Read all messages
            received = []
            for _ in range(len(messages)):
                msg = await read_message(reader, timeout=5.0)
                if msg:
                    received.append(msg)

            assert len(received) == 3
            assert [m.content for m in received] == ["First", "Second", "Third"]
        finally:
            writer.close()
            await writer.wait_closed()

    async def _create_stream_pair(self):
        """Create a connected pair of reader/writer streams."""
        # Start a local server and connect to it
        server_reader = None
        server_writer = None

        async def handle_client(reader, writer):
            nonlocal server_reader, server_writer
            server_reader = reader
            server_writer = writer

        server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
        addr = server.sockets[0].getsockname()

        # Connect client
        client_reader, client_writer = await asyncio.open_connection(addr[0], addr[1])

        # Wait for server to accept
        await asyncio.sleep(0.1)

        # Return client as writer (input), server as reader (output)
        # This simulates sending from client and reading on server
        assert server_reader is not None
        return server_reader, client_writer


class TestMessageTypes:
    """Tests for MessageType enum."""

    def test_all_message_types_have_values(self):
        """Ensure all message types have string values."""
        for msg_type in MessageType:
            assert isinstance(msg_type.value, str)
            assert len(msg_type.value) > 0

    def test_message_type_from_string(self):
        """Test creating MessageType from string value."""
        assert MessageType("user_message") == MessageType.USER_MESSAGE
        assert MessageType("assistant_message") == MessageType.ASSISTANT_MESSAGE
        assert MessageType("stream_chunk") == MessageType.STREAM_CHUNK

    def test_message_type_invalid_string(self):
        """Test that invalid string raises ValueError."""
        with pytest.raises(ValueError):
            MessageType("invalid_type")
