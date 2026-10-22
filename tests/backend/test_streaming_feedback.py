"""
Tests for Streaming Functionality and Agent Activity Feedback.

Critical tests for ensuring the aliens see real-time feedback:
- Streaming response chunks
- Agent activity updates
- Sub-agent delegation feedback
- SSE message integrity
"""

import asyncio

import pytest

from app.backend.chat.streaming import (
    AsyncIteratorize,
    Iteratorize,
    StreamingBuffer,
    is_stop_requested,
    reset_stop_flag,
    stop_everything,
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


class TestAgentActivityFeedback:
    """Tests for agent activity updates visible in UI."""

    def test_agent_info_runtime_status(self):
        """Test AgentInfo includes runtime status fields."""
        from app.backend.monitoring.state import AgentInfo

        agent = AgentInfo.from_dict(
            {
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
            }
        )

        assert agent.status == "busy"
        assert agent.status_message == "Processing request"
        assert agent.pid == 12345
        assert agent.last_heartbeat is not None

    def test_loop_info_creation(self):
        """Test LoopInfo for Planner/Executor status."""
        from app.backend.monitoring.state import LoopInfo

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
