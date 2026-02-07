"""
Tests for Streaming Functionality and Agent Activity Feedback.

Critical tests for ensuring the aliens see real-time feedback:
- Streaming response chunks
- Agent activity updates
- Sub-agent delegation feedback
- IPC message integrity
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from queue import Queue
from threading import Thread

from app.backend.streaming import (
    Iteratorize,
    AsyncIteratorize,
    StreamChunk,
    StreamingBuffer,
    StopNowException,
    stop_everything,
    reset_stop_flag,
    is_stop_requested,
    stream_with_callback,
)


class TestIteratorize:
    """Tests for converting callbacks to iterators."""

    def test_basic_callback_to_iterator(self):
        """Test converting a simple callback function to iterator."""
        def generate_numbers(callback):
            for i in range(5):
                callback(i)
            return "done"

        results = []
        for value in Iteratorize(generate_numbers):
            results.append(value)

        assert results == [0, 1, 2, 3, 4]

    def test_callback_with_args(self):
        """Test callback function with arguments."""
        def generate_with_prefix(prefix, callback):
            for i in range(3):
                callback(f"{prefix}-{i}")
            return "done"

        results = list(Iteratorize(generate_with_prefix, args=["test"]))
        assert results == ["test-0", "test-1", "test-2"]

    def test_callback_with_kwargs(self):
        """Test callback function with keyword arguments."""
        def generate_with_options(multiplier=1, callback=None):
            assert callback is not None
            for i in range(3):
                callback(i * multiplier)
            return "done"

        results = list(Iteratorize(generate_with_options, kwargs={"multiplier": 10}))
        assert results == [0, 10, 20]

    def test_result_property(self):
        """Test that result from wrapped function is accessible."""
        def return_value(callback):
            callback("chunk")
            return "final_result"

        iterator = Iteratorize(return_value)
        list(iterator)  # Consume iterator

        # Result should be available after iteration
        assert iterator.result == "final_result"

    def test_stop_method(self):
        """Test that stop() method works."""
        def slow_generate(callback):
            for i in range(1000):
                callback(i)
            return "done"

        iterator = Iteratorize(slow_generate)
        results = []

        for i, value in enumerate(iterator):
            results.append(value)
            if i >= 2:
                iterator.stop()
                break

        assert len(results) <= 4  # Should stop early

    def test_context_manager(self):
        """Test using Iteratorize as context manager."""
        def generate(callback):
            for i in range(5):
                callback(i)
            return "done"

        with Iteratorize(generate) as iterator:
            results = [next(iterator), next(iterator)]

        assert results == [0, 1]

    def test_exception_propagation(self):
        """Test that exceptions in the callback function propagate."""
        def failing_function(callback):
            callback("first")
            raise ValueError("Test error")

        iterator = Iteratorize(failing_function)

        with pytest.raises(ValueError, match="Test error"):
            list(iterator)


class TestStopFlag:
    """Tests for global stop flag."""

    def test_stop_flag_initial_state(self):
        """Test initial stop flag state."""
        reset_stop_flag()
        assert not is_stop_requested()

    def test_stop_flag_set(self):
        """Test setting stop flag."""
        reset_stop_flag()
        stop_everything()
        assert is_stop_requested()

    def test_stop_flag_reset(self):
        """Test resetting stop flag."""
        stop_everything()
        reset_stop_flag()
        assert not is_stop_requested()


class TestStreamChunk:
    """Tests for StreamChunk dataclass."""

    def test_stream_chunk_creation(self):
        """Test creating a stream chunk."""
        chunk = StreamChunk(content="hello", index=0)
        assert chunk.content == "hello"
        assert chunk.index == 0
        assert chunk.is_final is False
        assert chunk.metadata is None

    def test_stream_chunk_final(self):
        """Test final stream chunk."""
        chunk = StreamChunk(content="", index=10, is_final=True)
        assert chunk.is_final is True

    def test_stream_chunk_with_metadata(self):
        """Test stream chunk with metadata."""
        chunk = StreamChunk(
            content="test",
            index=5,
            metadata={"tokens": 100, "model": "gpt-4"}
        )
        assert chunk.metadata["tokens"] == 100


class TestStreamingBuffer:
    """Tests for StreamingBuffer."""

    def test_buffer_basic_add(self):
        """Test adding chunks to buffer."""
        buffer = StreamingBuffer(flush_threshold=10)
        result = buffer.add("hello")

        assert result is None  # Not at threshold yet
        assert buffer.has_content is True

    def test_buffer_auto_flush(self):
        """Test auto-flush when threshold reached."""
        buffer = StreamingBuffer(flush_threshold=10)

        # Add chunks until threshold
        buffer.add("1234567")
        result = buffer.add("890")  # Should trigger flush

        assert result == "1234567890"
        assert buffer.has_content is False

    def test_buffer_manual_flush(self):
        """Test manual flush."""
        buffer = StreamingBuffer(flush_threshold=100)
        buffer.add("hello ")
        buffer.add("world")

        content = buffer.flush()

        assert content == "hello world"
        assert buffer.has_content is False

    def test_buffer_empty_flush(self):
        """Test flushing empty buffer."""
        buffer = StreamingBuffer()
        content = buffer.flush()

        assert content == ""


class TestAsyncIteratorize:
    """Tests for async version of Iteratorize."""

    @pytest.mark.asyncio
    async def test_async_basic(self):
        """Test basic async iteration."""
        async def async_generate(callback):
            for i in range(3):
                await callback(i)
            return "done"

        iterator = AsyncIteratorize(async_generate)
        results = []

        async for value in iterator:
            results.append(value)

        assert results == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_async_stop(self):
        """Test stopping async iterator."""
        async def long_generate(callback):
            for i in range(100):
                await callback(i)
                await asyncio.sleep(0.01)
            return "done"

        iterator = AsyncIteratorize(long_generate)
        results = []

        async for value in iterator:
            results.append(value)
            if len(results) >= 3:
                await iterator.stop()
                break

        assert len(results) <= 4


class TestStreamWithCallback:
    """Tests for stream_with_callback helper."""

    @pytest.mark.asyncio
    async def test_stream_with_callbacks(self):
        """Test stream_with_callback invokes all callbacks."""
        chunks_received = []
        complete_result = None

        async def mock_generator():
            for chunk in ["hello", " ", "world"]:
                yield chunk

        def on_chunk(chunk, index):
            chunks_received.append((chunk, index))

        def on_complete(full_response):
            nonlocal complete_result
            complete_result = full_response

        result = await stream_with_callback(
            mock_generator(),
            on_chunk=on_chunk,
            on_complete=on_complete,
        )

        assert result == "hello world"
        assert complete_result == "hello world"
        assert len(chunks_received) == 3
        assert chunks_received[0] == ("hello", 0)

    @pytest.mark.asyncio
    async def test_stream_error_callback(self):
        """Test error callback is invoked on exception."""
        error_received = None

        async def failing_generator():
            yield "start"
            raise ValueError("Test error")

        def on_error(error):
            nonlocal error_received
            error_received = error

        with pytest.raises(ValueError):
            await stream_with_callback(
                failing_generator(),
                on_error=on_error,
            )

        assert error_received is not None
        assert "Test error" in str(error_received)


class TestIPCProtocol:
    """Tests for IPC protocol messages."""

    def test_user_message_creation(self):
        """Test creating user message."""
        from app.progenitor.ipc.protocol import user_message, MessageType

        msg = user_message("Hello, agent!")

        assert msg.type == MessageType.USER_MESSAGE
        assert msg.content == "Hello, agent!"

    def test_stream_chunk_message(self):
        """Test creating stream chunk message."""
        from app.progenitor.ipc.protocol import stream_chunk, MessageType

        msg = stream_chunk("token", request_id="req-123")

        assert msg.type == MessageType.STREAM_CHUNK
        assert msg.content == "token"
        assert msg.request_id == "req-123"

    def test_stream_end_message(self):
        """Test creating stream end message."""
        from app.progenitor.ipc.protocol import stream_end, MessageType

        msg = stream_end(request_id="req-123")

        assert msg.type == MessageType.STREAM_END
        assert msg.request_id == "req-123"
        assert msg.is_final is True

    def test_error_message(self):
        """Test creating error message."""
        from app.progenitor.ipc.protocol import error_message, MessageType

        msg = error_message("Something went wrong", error_code="ERR001")

        assert msg.type == MessageType.ERROR
        assert msg.content == "Something went wrong"
        assert msg.error_code == "ERR001"

    def test_heartbeat_message(self):
        """Test creating heartbeat message."""
        from app.progenitor.ipc.protocol import heartbeat, MessageType

        msg = heartbeat()

        assert msg.type == MessageType.HEARTBEAT

    def test_ready_message(self):
        """Test creating ready message."""
        from app.progenitor.ipc.protocol import ready_message, MessageType

        msg = ready_message()

        assert msg.type == MessageType.READY


class TestIPCEncoding:
    """Tests for IPC message encoding/decoding."""

    def test_encode_decode_roundtrip(self):
        """Test message survives encode/decode roundtrip."""
        from app.progenitor.ipc.protocol import (
            IPCMessage,
            MessageType,
            encode_message,
            decode_message,
            HEADER_SIZE,
        )

        original = IPCMessage(
            type=MessageType.STREAM_CHUNK,
            content="Hello world",
            request_id="req-456",
            is_final=False,
            metadata={"tokens": 5},
        )

        encoded = encode_message(original)
        # decode_message expects just the JSON bytes (no header)
        json_bytes = encoded[HEADER_SIZE:]
        decoded = decode_message(json_bytes)

        assert decoded.type == original.type
        assert decoded.content == original.content
        assert decoded.request_id == original.request_id
        assert decoded.is_final == original.is_final
        assert decoded.metadata == original.metadata

    def test_message_size_validation(self):
        """Test message size limits."""
        from app.progenitor.ipc.protocol import (
            IPCMessage,
            MessageType,
            encode_message,
            MAX_MESSAGE_SIZE,
        )

        # Create a message that's too large
        large_content = "x" * (MAX_MESSAGE_SIZE + 1000)
        msg = IPCMessage(type=MessageType.USER_MESSAGE, content=large_content)

        # Should raise error for oversized message
        with pytest.raises((ValueError, OverflowError)):
            encode_message(msg)


class TestAgentActivityFeedback:
    """Tests for agent activity updates visible in UI."""

    def test_agent_info_runtime_status(self):
        """Test AgentInfo includes runtime status fields."""
        from app.backend.state_reader import AgentInfo

        agent = AgentInfo.from_dict({
            "name": "test-agent",
            "kind": "a2a_agent",
            "version": "1.0.0",
            "description": "Test",
            "capabilities": [],
            "entrypoint": "python",
            "transport": "stdio",
            "enabled": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            # Runtime status
            "status": "busy",
            "status_message": "Processing request",
            "pid": 12345,
            "last_heartbeat": "2024-01-01T12:00:00Z",
        })

        assert agent.status == "busy"
        assert agent.status_message == "Processing request"
        assert agent.pid == 12345
        assert agent.last_heartbeat is not None

    def test_loop_info_creation(self):
        """Test LoopInfo for Planner/Executor status."""
        from app.backend.state_reader import LoopInfo

        planner = LoopInfo(
            name="Planner",
            status="active",
            last_activity="2024-01-01T12:00:00Z",
            current_task="Planning next action",
        )

        executor = LoopInfo(
            name="Executor",
            status="idle",
            last_activity="2024-01-01T11:59:00Z",
            current_task=None,
        )

        assert planner.status == "active"
        assert executor.status == "idle"


class TestSubAgentDelegation:
    """Tests for sub-agent task delegation feedback."""

    def test_task_response_structure(self):
        """Test task endpoint returns proper structure."""
        from fastapi.testclient import TestClient
        from app.backend.main import app

        client = TestClient(app)
        response = client.get("/api/ui/tasks")

        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "total" in data

    def test_backlog_item_priority(self):
        """Test backlog items have priority for ordering."""
        from app.backend.state_reader import BacklogItem

        high_priority = BacklogItem.from_dict({
            "id": "task-1",
            "item_type": "bug",
            "status": "pending",
            "description": "Critical fix",
            "priority": 10,
        })

        low_priority = BacklogItem.from_dict({
            "id": "task-2",
            "item_type": "feature",
            "status": "pending",
            "description": "Nice to have",
            "priority": 1,
        })

        assert high_priority.priority > low_priority.priority


class TestBuildStateTracking:
    """Tests for build state and phase tracking."""

    def test_build_phases_sequence(self):
        """Test all build phases are valid."""
        from app.backend.state_reader import BuildInfo

        # Valid phases in typical order
        phases = ["observe", "think", "plan", "build", "execute", "verify", "learn"]

        for phase in phases:
            build = BuildInfo(
                in_progress=True,
                phase=phase,
                iteration=1,
                score=0.5,
            )
            assert build.phase == phase

    def test_build_score_tracking(self):
        """Test build verification score tracking."""
        from app.backend.state_reader import BuildInfo

        build = BuildInfo.from_dict({
            "build_in_progress": True,
            "build_phase": "verify",
            "verification": {"current_score": 0.85},
            "iteration": {"current": 5},
        })

        assert build.score == 0.85
        assert build.iteration == 5

    def test_build_message_count(self):
        """Test build message count tracking."""
        from app.backend.state_reader import BuildInfo

        messages = [{"role": "user", "content": "msg"}] * 10

        build = BuildInfo.from_dict({
            "build_in_progress": True,
            "build_messages": messages,
        })

        assert build.message_count == 10


class TestChatAPIStreaming:
    """Tests for chat API streaming responses."""

    @patch('app.backend.main.get_ipc_client')
    def test_responses_endpoint_exists(self, mock_get_client):
        """Test responses endpoint accepts requests."""
        from fastapi.testclient import TestClient
        from app.backend.main import app

        # Mock the client to avoid IPC connection issues
        mock_client = AsyncMock()
        mock_client.chat_completion.return_value = {"error": "mocked"}
        mock_get_client.return_value = mock_client

        client = TestClient(app)
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "test",
                "stream": False,
            },
        )

        # May return error but endpoint should be reachable
        assert response.status_code in [200, 500, 503, 504]

    @patch('app.backend.main.get_ipc_client')
    def test_responses_api_endpoint_exists(self, mock_get_client):
        """Test responses endpoint accepts requests."""
        from fastapi.testclient import TestClient
        from app.backend.main import app

        # Mock the client to avoid IPC connection issues
        mock_client = AsyncMock()
        mock_client.respond.return_value = {"error": "mocked"}
        mock_get_client.return_value = mock_client

        client = TestClient(app)
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "test",
            },
        )

        # May return error but endpoint should be reachable
        assert response.status_code in [200, 400, 500, 503]


class TestRealTimeUpdates:
    """Tests for real-time update mechanisms."""

    def test_broadcast_event_puts_to_queue(self):
        """Test broadcast_event adds to subscriber queues."""
        from app.backend.ui_routes import _chat_subscribers, broadcast_chat_event
        import asyncio

        # Clear subscribers
        _chat_subscribers.clear()

        # Add test queue
        queue = asyncio.Queue()
        _chat_subscribers.append(queue)

        try:
            # Broadcast event
            broadcast_chat_event("test", {"data": "value"})

            # Verify event was added
            assert not queue.empty()
        finally:
            _chat_subscribers.remove(queue)

    def test_status_monitor_function_exists(self):
        """Test status monitor functions exist."""
        from app.backend.ui_routes import (
            start_status_monitor,
            _get_current_status,
        )

        # Functions should be callable
        assert callable(start_status_monitor)
        assert callable(_get_current_status)
