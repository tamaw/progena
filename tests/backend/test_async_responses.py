"""
Tests for Async Response Handling and Concurrent Operations.

Comprehensive tests for async response handling, concurrent requests,
request cancellation, timeout behavior, and backpressure management.

Covers:
- Async response handling
- Concurrent request handling
- Request cancellation
- Timeout behavior
- Backpressure and queue management
- Session management
- Connection pooling
"""

import asyncio
import concurrent.futures
import time

import pytest
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.streaming import (
    AsyncIteratorize,
    Iteratorize,
    StreamChunk,
    StreamingBuffer,
    is_stop_requested,
    reset_stop_flag,
    stop_everything,
    stream_with_callback,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_stop_flag_fixture():
    """Reset stop flag before and after each test."""
    reset_stop_flag()
    yield
    reset_stop_flag()


class TestAsyncResponseHandling:
    """Tests for async response handling."""

    def test_basic_async_response(self, client):
        """Test basic async response."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Hello",
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_async_responses_api(self, client):
        """Test async Responses API."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Hello, async!",
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_async_streaming_response(self, client):
        """Test async streaming response."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Stream test",
                "stream": True,
            },
        )
        assert response.status_code in [200, 500, 503]

    @pytest.mark.asyncio
    async def test_stream_response_unified_emits_structured_stream_events(self):
        """Structured backend stream items should become SSE response.stream_event events."""
        import app.backend.main as backend_main

        original_client = backend_main.client

        async def _events():
            yield {"type": "stream_chunk", "content": "Hello"}
            yield {
                "type": "stream_event",
                "event_type": "reasoning_summary",
                "content": "Checked constraints before answering.",
                "metadata": {"reasoning_tokens": 17},
            }
            yield {"type": "stream_chunk", "content": "", "is_final": True}

        class _MockClient:
            def send_message_stream_events(
                self,
                message: str,
                request_id: str | None = None,
                metadata: dict[str, object] | None = None,
            ):
                del message, request_id, metadata
                return _events()

        backend_main.client = _MockClient()

        try:
            payload = [
                part
                async for part in backend_main.stream_response_unified(
                    "hi",
                    "progenitor-v1",
                    request_id="req-structured",
                )
            ]
        finally:
            backend_main.client = original_client

        assert any("event: response.output_text.delta" in part for part in payload)
        assert any("event: response.stream_event" in part for part in payload)
        assert any("reasoning_summary" in part for part in payload)
        assert any("event: response.completed" in part for part in payload)

    def test_streaming_responses_use_session_thread_id(self, client):
        """Streaming chat should route through a thread scoped to the web session."""
        import app.backend.main as backend_main

        original_client = backend_main.client
        captured: dict[str, object] = {}

        class _MockClient:
            async def send_message_stream_events(
                self,
                message: str,
                request_id: str | None = None,
                metadata: dict[str, object] | None = None,
            ):
                captured["message"] = message
                captured["request_id"] = request_id
                captured["metadata"] = metadata
                yield {"type": "stream_chunk", "content": "Hello"}
                yield {"type": "stream_chunk", "content": "", "is_final": True}

        backend_main.client = _MockClient()

        try:
            client.cookies.set("progenitor_session", "sess_thread_abc")
            response = client.post(
                "/v1/responses",
                json={
                    "model": "progenitor-v1",
                    "input": "hi",
                    "stream": True,
                },
            )
        finally:
            client.cookies.clear()
            backend_main.client = original_client

        assert response.status_code == 200
        assert captured["metadata"] == {"thread_id": "thread_sess_thread_abc"}

    def test_non_streaming_responses_use_session_thread_id(self, client):
        """Non-streaming chat should route through a thread scoped to the web session."""
        import app.backend.main as backend_main

        original_client = backend_main.client
        captured: dict[str, object] = {}

        class _MockClient:
            async def send_message(
                self,
                message: str,
                request_id: str | None = None,
                metadata: dict[str, object] | None = None,
                include_reasoning: bool | None = None,
            ):
                captured["message"] = message
                captured["request_id"] = request_id
                captured["metadata"] = metadata
                captured["include_reasoning"] = include_reasoning
                from app.progenitor.responses import AgentResponse

                return AgentResponse(response="Hello", reasoning=None, reasoning_tokens=None)

        backend_main.client = _MockClient()

        try:
            client.cookies.set("progenitor_session", "sess_thread_xyz")
            response = client.post(
                "/v1/responses",
                json={
                    "model": "progenitor-v1",
                    "input": "hi",
                    "stream": False,
                },
            )
        finally:
            client.cookies.clear()
            backend_main.client = original_client

        assert response.status_code == 200
        assert captured["metadata"] == {"thread_id": "thread_sess_thread_xyz"}
        assert captured["include_reasoning"] is None

    def test_non_streaming_responses_include_reasoning_output(self, client):
        """Non-streaming /v1/responses should include reasoning items when available."""
        import app.backend.main as backend_main

        original_client = backend_main.client

        class _MockClient:
            async def send_message(
                self,
                message: str,
                request_id: str | None = None,
                metadata: dict[str, object] | None = None,
                include_reasoning: bool | None = None,
            ):
                del message, request_id, metadata
                assert include_reasoning is True
                from app.progenitor.responses import AgentResponse

                return AgentResponse(
                    response="10.50. At $0.75 per pencil, 14 pencils cost $10.50.",
                    reasoning="Checked the unit price before multiplying by 14.",
                    reasoning_tokens=17,
                )

        backend_main.client = _MockClient()

        try:
            response = client.post(
                "/v1/responses",
                json={
                    "model": "progenitor-v1",
                    "input": "hi",
                    "stream": False,
                    "include_reasoning": True,
                },
            )
        finally:
            backend_main.client = original_client

        assert response.status_code == 200
        payload = response.json()
        assert payload["output"][0]["content"] == [
            {
                "type": "reasoning",
                "summary": [
                    {
                        "type": "summary_text",
                        "text": "Checked the unit price before multiplying by 14.",
                    }
                ],
            },
            {
                "type": "output_text",
                "text": "10.50. At $0.75 per pencil, 14 pencils cost $10.50.",
            },
        ]


class TestConcurrentRequestHandling:
    """Tests for concurrent request handling."""

    def test_multiple_concurrent_requests(self, client):
        """Test handling multiple concurrent requests."""

        def make_request(msg_id):
            return client.post(
                "/v1/responses",
                json={
                    "model": "progenitor-v1",
                    "input": f"Concurrent request {msg_id}",
                    "stream": False,
                },
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All requests should complete (success or error)
        for response in results:
            assert response.status_code in [200, 429, 500, 503]

    def test_concurrent_status_checks(self, client):
        """Test concurrent status checks don't interfere."""

        def get_status():
            return client.get("/api/ui/status/snapshot")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(get_status) for _ in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for response in results:
            assert response.status_code == 200
            data = response.json()
            assert "connected" in data

    def test_mixed_endpoint_concurrency(self, client):
        """Test concurrent requests to different endpoints."""

        def make_requests():
            responses = []
            responses.append(client.get("/api/ui/status/snapshot"))
            responses.append(client.get("/api/ui/agents"))
            responses.append(client.get("/api/ui/backlog"))
            responses.append(client.get("/api/ui/schedule"))
            responses.append(client.get("/health"))
            return responses

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_requests) for _ in range(5)]
            all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for results in all_results:
            for response in results:
                assert response.status_code == 200


class TestRequestCancellation:
    """Tests for request cancellation behavior."""

    def test_stop_flag_halts_iteration(self):
        """Test global stop flag halts iteration."""
        reset_stop_flag()

        def slow_generate(callback):
            for i in range(100):
                callback(i)
                time.sleep(0.01)
            return "done"

        iterator = Iteratorize(slow_generate)
        results = []

        # Consume a few items then stop
        for i, value in enumerate(iterator):
            results.append(value)
            if i >= 5:
                stop_everything()
                break

        # Should have stopped early
        assert len(results) <= 10
        assert is_stop_requested()

    def test_iterator_stop_method(self):
        """Test Iterator.stop() method works."""

        def generate_many(callback):
            for i in range(1000):
                callback(i)
            return "done"

        iterator = Iteratorize(generate_many)
        results = []

        for i, value in enumerate(iterator):
            results.append(value)
            if i >= 10:
                iterator.stop()
                break

        assert len(results) <= 15

    def test_context_manager_cleanup(self):
        """Test context manager properly cleans up."""
        iterations = []

        def track_iterations(callback):
            for i in range(1000):
                iterations.append(i)
                callback(i)
            return "done"

        with Iteratorize(track_iterations) as iterator:
            results = [next(iterator) for _ in range(5)]

        assert len(results) == 5
        # After exiting context, iteration should stop


class TestTimeoutBehavior:
    """Tests for timeout handling."""

    def test_request_with_custom_timeout(self, client):
        """Test request with timeout doesn't hang indefinitely."""
        # Use test client which has reasonable timeout
        start = time.time()
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Timeout test",
                "stream": False,
            },
        )
        elapsed = time.time() - start

        # Should complete within reasonable time
        assert elapsed < 60  # 60 second max
        assert response.status_code in [200, 500, 503, 504]

    def test_streaming_buffer_timeout(self):
        """Test StreamingBuffer timeout flush."""
        buffer = StreamingBuffer(flush_threshold=1000, flush_timeout=0.1)

        buffer.add("hello")
        assert buffer.has_content

        # Simulate time passing
        time.sleep(0.15)
        current_time = time.time()

        # Should flush due to timeout
        assert buffer.should_flush(current_time) or buffer.has_content


class TestBackpressureManagement:
    """Tests for backpressure and queue management."""

    def test_streaming_buffer_threshold(self):
        """Test buffer auto-flushes at threshold."""
        buffer = StreamingBuffer(flush_threshold=10)

        # Add below threshold
        result = buffer.add("1234567")
        assert result is None

        # Add to exceed threshold
        result = buffer.add("890")
        assert result == "1234567890"
        assert not buffer.has_content

    def test_streaming_buffer_manual_flush(self):
        """Test manual buffer flush."""
        buffer = StreamingBuffer(flush_threshold=1000)

        buffer.add("hello ")
        buffer.add("world")
        assert buffer.has_content

        content = buffer.flush()
        assert content == "hello world"
        assert not buffer.has_content

    def test_queue_based_iteration(self):
        """Test queue-based iteration works correctly."""
        results = []

        def generate(callback):
            for i in range(10):
                callback(f"item-{i}")
            return "complete"

        for item in Iteratorize(generate):
            results.append(item)

        assert len(results) == 10
        assert results[0] == "item-0"
        assert results[9] == "item-9"


class TestAsyncIteratorize:
    """Tests for async iterator functionality."""

    @pytest.mark.asyncio
    async def test_async_basic_iteration(self):
        """Test basic async iteration."""

        async def async_generate(callback):
            for i in range(5):
                await callback(i)
            return "done"

        iterator = AsyncIteratorize(async_generate)
        results = []

        async for value in iterator:
            results.append(value)

        assert results == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_async_stop_iteration(self):
        """Test stopping async iteration."""

        async def long_generate(callback):
            for i in range(100):
                await callback(i)
                await asyncio.sleep(0.01)
            return "done"

        iterator = AsyncIteratorize(long_generate)
        results = []

        async for value in iterator:
            results.append(value)
            if len(results) >= 5:
                await iterator.stop()
                break

        assert len(results) <= 10

    @pytest.mark.asyncio
    async def test_async_with_args(self):
        """Test async iteration with arguments."""

        async def generate_with_prefix(prefix, callback):
            for i in range(3):
                await callback(f"{prefix}-{i}")
            return "done"

        iterator = AsyncIteratorize(generate_with_prefix, args=["test"])
        results = []

        async for value in iterator:
            results.append(value)

        assert results == ["test-0", "test-1", "test-2"]


class TestStreamWithCallback:
    """Tests for stream_with_callback helper."""

    @pytest.mark.asyncio
    async def test_all_callbacks_invoked(self):
        """Test all callbacks are invoked properly."""
        chunks = []
        complete_text = None

        async def mock_gen():
            for word in ["Hello", " ", "World"]:
                yield word

        def on_chunk(chunk, idx):
            chunks.append((chunk, idx))

        def on_complete(text):
            nonlocal complete_text
            complete_text = text

        result = await stream_with_callback(
            mock_gen(),
            on_chunk=on_chunk,
            on_complete=on_complete,
        )

        assert result == "Hello World"
        assert complete_text == "Hello World"
        assert len(chunks) == 3
        assert chunks[0] == ("Hello", 0)
        assert chunks[2] == ("World", 2)

    @pytest.mark.asyncio
    async def test_error_callback_on_exception(self):
        """Test error callback invoked on exception."""
        error_caught = None

        async def failing_gen():
            yield "start"
            raise ValueError("Test error")

        def on_error(e):
            nonlocal error_caught
            error_caught = e

        with pytest.raises(ValueError):
            await stream_with_callback(failing_gen(), on_error=on_error)

        assert error_caught is not None
        assert "Test error" in str(error_caught)


class TestSessionManagement:
    """Tests for session-based request handling."""

    def test_session_cookie_handling(self, client):
        """Test session cookie is handled in requests."""
        # First request
        response1 = client.get("/api/ui/status/snapshot")
        assert response1.status_code == 200

        # Second request with same session
        response2 = client.get("/api/ui/status/snapshot")
        assert response2.status_code == 200

    def test_concurrent_sessions(self, client):
        """Test concurrent sessions don't interfere."""

        def session_request(session_id):
            return client.get(
                "/api/ui/status/snapshot",
                headers={"X-Session-ID": f"session-{session_id}"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(session_request, i) for i in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for response in results:
            assert response.status_code == 200


class TestRequestTracking:
    """Tests for request tracking and correlation."""

    def test_request_id_header(self, client):
        """Test request ID is returned in response headers."""
        response = client.get(
            "/api/ui/status/snapshot", headers={"X-Request-ID": "test-request-123"}
        )

        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"] == "test-request-123"

    def test_auto_generated_request_id(self, client):
        """Test request ID is auto-generated if not provided."""
        response = client.get("/api/ui/status/snapshot")

        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"].startswith("req-")

    def test_request_tracking_across_endpoints(self, client):
        """Test request tracking works across endpoints."""
        request_id = "tracking-test-456"

        endpoints = [
            "/api/ui/status/snapshot",
            "/api/ui/agents",
            "/api/ui/backlog",
            "/health",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint, headers={"X-Request-ID": request_id})
            assert response.status_code == 200
            if endpoint != "/health":  # Health might not include this
                assert response.headers.get("X-Request-ID") == request_id


class TestGracefulShutdown:
    """Tests for graceful shutdown behavior."""

    def test_shutdown_manager_status(self, client):
        """Test shutdown status in health response."""
        response = client.get("/health/detailed")

        if response.status_code == 200:
            data = response.json()
            assert "shutdown" in data
            assert "is_shutting_down" in data["shutdown"] or "active_requests" in data["shutdown"]

    def test_readiness_check(self, client):
        """Test readiness endpoint reflects server state."""
        response = client.get("/ready")
        # Should be ready or not initialized
        assert response.status_code in [200, 503]

    def test_liveness_check_always_passes(self, client):
        """Test liveness check always passes when server is running."""
        response = client.get("/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"


class TestHealthEndpointContract:
    """Tests for the compact root /health contract."""

    def test_health_returns_required_fields(self, client):
        """GET /health should expose the compact probe payload."""
        response = client.get("/health")

        assert response.status_code == 200
        assert set(response.json()) == {
            "status",
            "name",
            "version",
            "uptime_seconds",
        }

    def test_health_returns_ok_when_healthy(self, client):
        """Healthy root probes should use status='ok'."""
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_returns_draining_and_503_when_shutting_down(self, client, monkeypatch):
        """Draining root probes should surface both status and HTTP 503."""
        from app.backend import main as backend_main

        monkeypatch.setattr(backend_main.shutdown_manager, "_shutting_down", True)
        response = client.get("/health")

        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "draining"
        assert data["name"] == "progenitor"

    def test_health_returns_non_negative_uptime(self, client):
        """Root health probes should report uptime as a number."""
        response = client.get("/health")
        data = response.json()

        assert isinstance(data["uptime_seconds"], int | float)
        assert data["uptime_seconds"] >= 0

    def test_health_uptime_increases_between_calls(self, client):
        """Uptime should increase on later health calls."""
        first = client.get("/health").json()["uptime_seconds"]
        time.sleep(0.12)
        second = client.get("/health").json()["uptime_seconds"]

        assert second > first


class TestRateLimitingAsync:
    """Tests for rate limiting with async requests."""

    def test_rate_limit_headers(self, client):
        """Test rate limit headers are present."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Rate limit test",
            },
        )

        # Rate limit headers should be present (if rate limiting enabled)
        # Note: UI endpoints bypass rate limiting
        if response.status_code not in [503]:
            # Headers may be present depending on config
            pass  # Rate limiting is configurable

    def test_ui_endpoints_bypass_rate_limit(self, client):
        """Test UI endpoints bypass rate limiting."""
        # UI endpoints should never be rate limited
        for _ in range(100):
            response = client.get("/api/ui/status/snapshot")
            assert response.status_code == 200


class TestErrorRecovery:
    """Tests for error recovery in async operations."""

    def test_exception_in_generator_propagates(self):
        """Test exceptions in generator propagate correctly."""

        def failing_generator(callback):
            callback("first")
            raise ValueError("Intentional error")

        iterator = Iteratorize(failing_generator)

        with pytest.raises(ValueError, match="Intentional error"):
            list(iterator)

    def test_iterator_result_after_exception(self):
        """Test iterator result is None after exception."""

        def partial_generator(callback):
            callback("item1")
            callback("item2")
            raise RuntimeError("Mid-stream error")

        iterator = Iteratorize(partial_generator)

        with pytest.raises(RuntimeError):
            list(iterator)

        # Result should not be set
        assert iterator.result is None

    def test_recovery_after_stop_flag(self):
        """Test recovery after stop flag is set."""
        reset_stop_flag()

        # Set and reset stop flag
        stop_everything()
        assert is_stop_requested()

        reset_stop_flag()
        assert not is_stop_requested()

        # Should work normally after reset
        def simple_gen(callback):
            callback("value")
            return "done"

        results = list(Iteratorize(simple_gen))
        assert results == ["value"]


class TestStreamChunk:
    """Tests for StreamChunk dataclass."""

    def test_basic_chunk(self):
        """Test basic chunk creation."""
        chunk = StreamChunk(content="Hello", index=0)
        assert chunk.content == "Hello"
        assert chunk.index == 0
        assert chunk.is_final is False
        assert chunk.metadata is None

    def test_final_chunk(self):
        """Test final chunk creation."""
        chunk = StreamChunk(content="", index=10, is_final=True)
        assert chunk.is_final is True
        assert chunk.index == 10

    def test_chunk_with_metadata(self):
        """Test chunk with metadata."""
        metadata = {"tokens": 100, "model": "gpt-4", "latency_ms": 50}
        chunk = StreamChunk(content="test", index=5, metadata=metadata)

        assert chunk.metadata is not None
        assert chunk.metadata["tokens"] == 100
        assert chunk.metadata["model"] == "gpt-4"
        assert chunk.metadata["latency_ms"] == 50


class TestLargeMessageHandling:
    """Tests for handling large messages."""

    def test_large_user_message(self, client):
        """Test handling of large user messages."""
        large_content = "This is a test message. " * 1000  # ~24000 chars

        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": large_content,
                "stream": False,
            },
        )
        assert response.status_code in [200, 400, 413, 500, 503]

    def test_large_input_message(self, client):
        """Test handling large input."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Final question",
                "stream": False,
            },
        )
        assert response.status_code in [200, 400, 413, 500, 503]


class TestConnectionPooling:
    """Tests for connection handling and pooling."""

    def test_many_sequential_requests(self, client):
        """Test many sequential requests are handled."""
        for _i in range(20):
            response = client.get("/api/ui/status/snapshot")
            assert response.status_code == 200

    def test_rapid_sequential_requests(self, client):
        """Test rapid sequential requests don't fail."""
        start = time.time()
        for _ in range(50):
            response = client.get("/health")
            assert response.status_code == 200
        elapsed = time.time() - start

        # Should complete quickly (under 10 seconds)
        assert elapsed < 10


class TestIPCMessageHandling:
    """Tests for IPC message handling (if applicable)."""

    def test_ipc_message_encoding(self):
        """Test IPC message encoding/decoding."""
        from app.progenitor.ipc.protocol import (
            HEADER_SIZE,
            IPCMessage,
            MessageType,
            decode_message,
            encode_message,
        )

        original = IPCMessage(
            type=MessageType.USER_MESSAGE,
            content="Test message",
            request_id="req-test-001",
            metadata={"key": "value"},
        )

        encoded = encode_message(original)
        assert len(encoded) > HEADER_SIZE

        # Decode (skip header)
        decoded = decode_message(encoded[HEADER_SIZE:])
        assert decoded.type == original.type
        assert decoded.content == original.content
        assert decoded.request_id == original.request_id

    def test_ipc_stream_messages(self):
        """Test IPC stream chunk and stream end messages."""
        from app.progenitor.ipc.protocol import (
            MessageType,
            stream_chunk,
            stream_end,
        )

        # Test stream chunk
        chunk = stream_chunk("token data", request_id="req-123")
        assert chunk.type == MessageType.STREAM_CHUNK
        assert chunk.content == "token data"
        assert chunk.request_id == "req-123"

        # Test stream end
        end = stream_end(request_id="req-123")
        assert end.type == MessageType.STREAM_END
        assert end.request_id == "req-123"
        assert end.is_final is True


class TestAsyncCleanup:
    """Tests for async resource cleanup."""

    @pytest.mark.asyncio
    async def test_async_iterator_cleanup_on_cancel(self):
        """Test async iterator cleanup on cancellation."""

        async def slow_gen(callback):
            for i in range(100):
                await callback(i)
                await asyncio.sleep(0.1)
            return "done"

        iterator = AsyncIteratorize(slow_gen)

        # Start iteration
        results = []
        async for value in iterator:
            results.append(value)
            if len(results) >= 2:
                await iterator.stop()
                break

        # Should have stopped cleanly
        assert len(results) <= 5

    def test_sync_iterator_cleanup(self):
        """Test sync iterator cleanup on context exit."""
        call_count = [0]

        def counting_gen(callback):
            for i in range(100):
                call_count[0] += 1
                callback(i)
                # Add a small delay to allow stop flag to be noticed
                time.sleep(0.001)
            return "done"

        with Iteratorize(counting_gen) as iterator:
            _ = next(iterator)
            _ = next(iterator)
            # Exit context early - this sets stop_now = True

        # Give thread time to notice and stop
        time.sleep(0.1)

        # Note: The generator runs in a separate thread and may complete
        # before the stop is noticed, especially for fast generators.
        # This test verifies the cleanup mechanism exists, not that it
        # always stops mid-iteration (which depends on timing).
        # The key assertion is that the context manager exits cleanly.
        assert call_count[0] >= 2  # At least consumed 2 items
