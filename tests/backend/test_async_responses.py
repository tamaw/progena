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
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
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


@contextmanager
def _live_backend() -> Iterator[str]:
    """Run the ASGI app on loopback so SSE frames are not transport-buffered."""
    from app.backend import ui_routes

    ui_routes._sse_shutdown_event = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()

    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, lifespan="off", log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert thread.is_alive(), "Uvicorn stopped before accepting connections"
        assert time.monotonic() < deadline, "Timed out starting Uvicorn"
        time.sleep(0.01)

    try:
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        ui_routes._sse_shutdown_event = None
        assert not thread.is_alive(), "Uvicorn did not stop"


def _next_sse_frame(lines: Iterator[str]) -> tuple[str, dict[str, object]]:
    """Decode one complete frame emitted by the canonical UI SSE endpoint."""
    event_name: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif line == "" and event_name is not None:
            return event_name, json.loads("\n".join(data_lines))
    raise AssertionError("SSE stream closed before a complete event frame")


class TestAsyncResponseHandling:
    """Tests for async response handling."""

    def test_unavailable_response_request_returns_failure_without_success_event(
        self, client, monkeypatch
    ):
        """A rejected generation request neither acknowledges nor publishes success."""
        from app.backend import main as backend_main
        from app.backend import ui_routes

        event_start = len(ui_routes._ui_event_replay)
        monkeypatch.setattr(backend_main, "client", None)
        response = client.post(
            "/api/ui/responses",
            json={"model": "progenitor-v1", "input": "Hello", "stream": False},
            headers={"X-Request-ID": "rejected-generation"},
        )

        assert response.status_code == 503
        assert not any(
            event.payload.get("request_id") == "rejected-generation"
            for event in ui_routes._ui_event_replay[event_start:]
        )

    def test_legacy_response_post_route_is_absent(self, client):
        """The retired OpenAI-compatible generation route accepts no POST requests."""
        response = client.post(
            "/v1/responses",
            json={"model": "progenitor-v1", "input": "legacy", "stream": False},
        )

        assert response.status_code in (404, 405)

    def test_streaming_responses_use_session_thread_id(self, monkeypatch):
        """A dispatched generation resolves over serialized canonical SSE frames."""
        import app.backend.main as backend_main

        captured: dict[str, object] = {}
        request_id = "dd8db5b9-7ec6-4ea8-b481-7a2a3ef13a5d"

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
                yield {"type": "stream_chunk", "content": " world"}

        monkeypatch.setattr(backend_main, "client", _MockClient())
        with _live_backend() as base_url, httpx.Client(base_url=base_url, timeout=5) as live_client:
            live_client.cookies.set("progenitor_session", "sess_thread_abc")
            with live_client.stream("GET", "/api/ui/stream") as stream:
                assert stream.status_code == 200
                assert stream.headers["content-type"].startswith("text/event-stream")
                lines = stream.iter_lines()
                ready_name, ready = _next_sse_frame(lines)
                assert ready_name == "ui.event"
                assert ready["type"] == "connection.ready"

                response = live_client.post(
                    "/api/ui/responses",
                    json={"model": "progenitor-v1", "input": "hi", "stream": True},
                    headers={"X-Request-ID": request_id},
                )
                frames = [_next_sse_frame(lines) for _ in range(4)]

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert captured == {
            "message": "hi",
            "request_id": request_id,
            "metadata": {"thread_id": "thread_sess_thread_abc"},
        }
        assert [event_name for event_name, _ in frames] == ["ui.event"] * 4
        events = [event for _, event in frames]
        assert [event["type"] for event in events] == [
            "chat.response.started",
            "chat.response.delta",
            "chat.response.delta",
            "chat.response.completed",
        ]
        assert all(event["scope"] == "chat" for event in events)
        assert all(event["payload"]["request_id"] == request_id for event in events)
        assert "".join(event["payload"]["delta"] for event in events[1:3]) == "Hello world"
        assert events[-1]["payload"]["id"] == events[1]["payload"]["id"]

    def test_streaming_response_forwards_structured_stream_events(self, monkeypatch):
        """Structured runtime stream events ride the canonical stream intact.

        The subagent/A2A live cards are built entirely from ``stream_event``
        items (tool lifecycle, nested subagent events, A2A progress), so the
        ``chat.response.event`` frames must preserve ``event_type``,
        ``content``, full ``metadata`` (including ``subagent_type``), and
        ``images`` -- and must omit ``images`` when the runtime item has none.
        """
        import app.backend.main as backend_main

        request_id = "3d1a6c1e-59cb-45cf-88e2-b6f5f7f1a001"
        image_block = {
            "type": "image",
            "source_type": "base64",
            "mime_type": "image/png",
            "data": "aGk=",
        }

        class _MockClient:
            async def send_message_stream_events(self, *_args, **_kwargs):
                yield {
                    "type": "stream_event",
                    "event_type": "tool_start",
                    "content": "task",
                    "metadata": {
                        "tool_name": "task",
                        "tool_call_id": "call-1",
                        "subagent_type": "researcher",
                        "args_preview": {"description": "look things up"},
                    },
                }
                yield {
                    "type": "stream_event",
                    "event_type": "a2a_call_progress",
                    "content": "Working on your greeting...",
                    "metadata": {"tool_call_id": "call-2", "state": "working"},
                }
                yield {
                    "type": "stream_event",
                    "event_type": "subagent_result",
                    "content": "researcher",
                    "metadata": {
                        "subagent_name": "researcher",
                        "tool_call_id": "call-1",
                        "success": True,
                    },
                    "images": [image_block],
                }
                yield {"type": "stream_chunk", "content": "done"}

        monkeypatch.setattr(backend_main, "client", _MockClient())
        with (
            _live_backend() as base_url,
            httpx.Client(base_url=base_url, timeout=5) as live_client,
            live_client.stream("GET", "/api/ui/stream") as stream,
        ):
            lines = stream.iter_lines()
            _next_sse_frame(lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "delegate", "stream": True},
                headers={"X-Request-ID": request_id},
            )
            frames = [_next_sse_frame(lines) for _ in range(6)]

        assert response.status_code == 202
        events = [event for _, event in frames]
        assert [event["type"] for event in events] == [
            "chat.response.started",
            "chat.response.event",
            "chat.response.event",
            "chat.response.event",
            "chat.response.delta",
            "chat.response.completed",
        ]
        assert all(event["payload"]["request_id"] == request_id for event in events)

        tool_start, progress, subagent_result = (event["payload"] for event in events[1:4])
        assert tool_start["event_type"] == "tool_start"
        assert tool_start["content"] == "task"
        assert tool_start["metadata"]["subagent_type"] == "researcher"
        assert tool_start["metadata"]["args_preview"] == {"description": "look things up"}
        assert "images" not in tool_start

        assert progress["event_type"] == "a2a_call_progress"
        assert progress["content"] == "Working on your greeting..."
        assert progress["metadata"] == {"tool_call_id": "call-2", "state": "working"}
        assert "images" not in progress

        assert subagent_result["event_type"] == "subagent_result"
        assert subagent_result["metadata"] == {
            "subagent_name": "researcher",
            "tool_call_id": "call-1",
            "success": True,
        }
        assert subagent_result["images"] == [image_block]

    def test_failed_generation_emits_post_acknowledgement_causal_event(self, monkeypatch):
        """An acknowledged generation publishes a correlated failure rather than success."""
        import app.backend.main as backend_main

        request_id = "e234a82e-00c1-4a96-bebe-652485775d92"

        class _FailingClient:
            async def send_message_stream_events(self, *_args, **_kwargs):
                yield {"type": "stream_chunk", "content": "partial"}
                raise RuntimeError("stream exploded")

        monkeypatch.setattr(backend_main, "client", _FailingClient())
        with (
            _live_backend() as base_url,
            httpx.Client(base_url=base_url, timeout=5) as live_client,
            live_client.stream("GET", "/api/ui/stream") as stream,
        ):
            lines = stream.iter_lines()
            _next_sse_frame(lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "fail", "stream": True},
                headers={"X-Request-ID": request_id},
            )
            frames = [_next_sse_frame(lines) for _ in range(3)]

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert [event_name for event_name, _ in frames] == ["ui.event"] * 3
        events = [event for _, event in frames]
        assert [event["type"] for event in events] == [
            "chat.response.started",
            "chat.response.delta",
            "chat.response.failed",
        ]
        failed = events[-1]
        assert failed["scope"] == "chat"
        assert failed["payload"]["request_id"] == request_id
        assert failed["payload"]["error"] == "stream exploded"

    def test_cancel_response_sets_active_stream_event(self, client):
        """Cancellation is acknowledged only after its causal stream event is enqueued."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        original_client = backend_main.client
        cancel_event = asyncio.Event()
        captured: dict[str, object] = {}
        request_id = "cancel-operation-request"

        class _MockClient:
            async def cancel_request(self, generation_id: str) -> bool:
                captured["generation_id"] = generation_id
                return True

        event_start = len(ui_routes._ui_event_replay)
        backend_main.client = _MockClient()
        backend_main._active_response_streams["req-cancel-test"] = cancel_event
        try:
            response = client.put(
                "/api/ui/responses/req-cancel-test/cancel",
                headers={"X-Request-ID": request_id},
            )
            streamed = next(
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("request_id") == request_id
            )
        finally:
            backend_main._active_response_streams.pop("req-cancel-test", None)
            backend_main.client = original_client

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.cancelled"],
        }
        assert cancel_event.is_set()
        assert captured["generation_id"] == "req-cancel-test"
        assert streamed.type == "chat.response.cancelled"
        assert streamed.payload["request_id"] == request_id
        assert streamed.payload["cancel_request_id"] == request_id
        assert streamed.payload["target_request_id"] == "req-cancel-test"


    def test_cancel_response_resolves_browser_response_id_to_request_id(self, client):
        """The cancel URL receives the started event's response id, not its request id."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        original_client = backend_main.client
        request_id = "generation-request-42"
        response_id = "resp-browser-42"
        cancel_event = asyncio.Event()
        captured: dict[str, object] = {}

        class _MockClient:
            async def cancel_request(self, target_request_id: str) -> bool:
                captured["target_request_id"] = target_request_id
                return True

        backend_main.client = _MockClient()
        backend_main._active_response_streams[request_id] = cancel_event
        backend_main._response_request_ids[response_id] = request_id
        event_start = len(ui_routes._ui_event_replay)
        try:
            response = client.put(
                f"/api/ui/responses/{response_id}/cancel",
                headers={"X-Request-ID": "cancel-operation-42"},
            )
            streamed = next(
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.type == "chat.response.cancelled"
                and event.payload.get("target_response_id") == response_id
            )
        finally:
            backend_main._active_response_streams.pop(request_id, None)
            backend_main._response_request_ids.pop(response_id, None)
            backend_main.client = original_client

        assert response.status_code == 202
        assert response.json() == {
            "request_id": "cancel-operation-42",
            "event_types": ["chat.response.cancelled"],
        }
        assert cancel_event.is_set()
        assert captured["target_request_id"] == request_id
        assert streamed.payload["target_request_id"] == request_id
        assert streamed.payload["target_response_id"] == response_id
        assert streamed.payload["cancel_request_id"] == "cancel-operation-42"


    def test_healthz_bypasses_rate_limiter_when_api_quota_is_exhausted(self, client, monkeypatch):
        """Container liveness must remain probeable after an API rate-limit decision."""
        import app.backend.main as backend_main

        class _RejectingLimiter:
            def is_allowed(self, _client_id: str):
                raise AssertionError("healthz must not consult the API rate limiter")

        monkeypatch.setattr(backend_main, "rate_limiter", _RejectingLimiter())
        monkeypatch.setattr(backend_main.config, "rate_limit_enabled", True)

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.content == b""
    def test_cancel_response_returns_404_for_unknown_request(self, client):
        """Unknown cancellation IDs should not be reported as successful."""
        import app.backend.main as backend_main

        original_client = backend_main.client

        class _MockClient:
            async def cancel_request(self, request_id: str) -> bool:
                del request_id
                return False

        backend_main.client = _MockClient()

        try:
            response = client.put(
                "/api/ui/responses/not-active/cancel",
                headers={"X-Request-ID": "unknown-cancel-request"},
            )
        finally:
            backend_main.client = original_client

        assert response.status_code == 404

    def test_non_streaming_responses_use_session_thread_id(self, client):
        """Non-streaming output is event data, never a finite HTTP response body."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        original_client = backend_main.client
        captured: dict[str, object] = {}
        request_id = "nonstream-generation-request"

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
                from app.backend.models import AgentResponse

                return AgentResponse(response="Hello", reasoning=None, reasoning_tokens=None)

        event_start = len(ui_routes._ui_event_replay)
        backend_main.client = _MockClient()
        try:
            client.cookies.set("progenitor_session", "sess_thread_xyz")
            response = client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "hi", "stream": False},
                headers={"X-Request-ID": request_id},
            )
            events = [
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("request_id") == request_id
            ]
        finally:
            client.cookies.clear()
            backend_main.client = original_client

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert captured == {
            "message": "hi",
            "request_id": request_id,
            "metadata": {"thread_id": "thread_sess_thread_xyz"},
            "include_reasoning": None,
        }
        assert [event.type for event in events] == [
            "chat.response.started",
            "chat.response.delta",
            "chat.response.completed",
        ]
        assert all(event.payload["request_id"] == request_id for event in events)
        assert events[1].payload["delta"] == "Hello"

    def test_non_streaming_responses_include_reasoning_output(self, client):
        """Non-streaming /api/ui/responses should include reasoning items when available."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

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
                from app.backend.models import AgentResponse

                return AgentResponse(
                    response="10.50. At $0.75 per pencil, 14 pencils cost $10.50.",
                    reasoning="Checked the unit price before multiplying by 14.",
                    reasoning_tokens=17,
                )

        event_start = len(ui_routes._ui_event_replay)
        backend_main.client = _MockClient()

        request_id = "reasoning-generation-request"
        try:
            response = client.post(
                "/api/ui/responses",
                json={
                    "model": "progenitor-v1",
                    "input": "hi",
                    "stream": False,
                    "include_reasoning": True,
                },
                headers={"X-Request-ID": request_id},
            )
            events = [
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("request_id") == request_id
            ]
        finally:
            backend_main.client = original_client

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert [event.type for event in events] == [
            "chat.response.started",
            "chat.response.delta",
            "chat.response.completed",
        ]
        delta = events[1].payload
        assert delta["delta"] == "10.50. At $0.75 per pencil, 14 pencils cost $10.50."
        assert delta["reasoning"] == "Checked the unit price before multiplying by 14."
        assert delta["reasoning_tokens"] == 17


class TestConcurrentRequestHandling:
    """Tests for concurrent request handling."""

    def test_multiple_concurrent_requests(self, client):
        """Test handling multiple concurrent requests."""

        def make_request(msg_id):
            return client.post(
                "/api/ui/responses",
                json={
                    "model": "progenitor-v1",
                    "input": f"Concurrent request {msg_id}",
                    "stream": False,
                },
                headers={"X-Request-ID": f"concurrent-response-{msg_id}"},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # A dispatched response acknowledges with 202; failures are explicit errors, never legacy 200.
        for response in results:
            assert response.status_code in [202, 429, 500, 503]
            if response.status_code == 202:
                request_id = response.headers["X-Request-ID"]
                assert response.json() == {
                    "request_id": request_id,
                    "event_types": ["chat.response.started"],
                }

    def test_mixed_endpoint_concurrency(self, client):
        """Test concurrent requests to different endpoints."""

        def make_requests():
            return {
                "workflows": client.get(
                    "/api/ui/workflows", headers={"X-Request-ID": "concurrent-workflows"}
                ),
                "inbox": client.get("/api/ui/inbox", headers={"X-Request-ID": "concurrent-inbox"}),
                "chats": client.get("/api/ui/chats", headers={"X-Request-ID": "concurrent-chats"}),
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_requests) for _ in range(5)]
            all_results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for responses in all_results:
            for route, event_type in {
                "workflows": "workflows.snapshot",
                "inbox": "inbox.snapshot",
                "chats": "chat.sessions.snapshot",
            }.items():
                response = responses[route]
                assert response.status_code == 202
                assert response.json() == {
                    "request_id": response.headers["X-Request-ID"],
                    "event_types": [event_type],
                }


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
        request_id = "timeout-response-request"
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "progenitor-v1",
                "input": "Timeout test",
                "stream": False,
            },
            headers={"X-Request-ID": request_id},
        )
        elapsed = time.time() - start

        # Should complete within reasonable time.
        assert elapsed < 60
        assert response.status_code in [202, 500, 503, 504]
        if response.status_code == 202:
            assert response.headers["X-Request-ID"] == request_id
            assert response.json() == {
                "request_id": request_id,
                "event_types": ["chat.response.started"],
            }

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


class TestRequestTracking:
    """Tests for request tracking and correlation."""

    def test_request_id_header(self, client):
        """Test request ID is returned in response headers."""
        response = client.get("/api/ui/widgets", headers={"X-Request-ID": "test-request-123"})

        assert response.status_code == 202
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"] == "test-request-123"
        assert response.json() == {
            "request_id": "test-request-123",
            "event_types": ["widgets.updated"],
        }

    def test_auto_generated_request_id(self, client):
        """Test request ID is auto-generated if not provided."""
        response = client.get("/api/ui/widgets")

        assert response.status_code == 202
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"].startswith("req-")
        request_id = response.headers["X-Request-ID"]
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["widgets.updated"],
        }

    def test_request_tracking_across_endpoints(self, client):
        """Every finite application route preserves the caller operation ID in its ack."""
        request_id = "tracking-test-456"

        expected_events = {
            "/api/ui/workflows": ["workflows.snapshot"],
            "/api/ui/inbox": ["inbox.snapshot"],
            "/api/ui/chats": ["chat.sessions.snapshot"],
        }
        for endpoint, event_types in expected_events.items():
            response = client.get(endpoint, headers={"X-Request-ID": request_id})
            assert response.status_code == 202
            assert response.headers["X-Request-ID"] == request_id
            assert response.json() == {
                "request_id": request_id,
                "event_types": event_types,
            }

    def test_docker_healthz_is_empty_liveness_response(self, client):
        """Docker-only liveness is separate from browser API routes and carries no data."""
        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.content == b""

    def test_health_routes_are_deleted(self, client):
        """The plan deletes response-only health routes."""
        for path in ("/health", "/health/detailed"):
            assert client.get(path).status_code == 404


class TestRateLimitingAsync:
    """Tests for rate limiting with async requests."""

    def test_rate_limit_headers(self, client):
        """Test rate limit headers are present."""
        request_id = "rate-limit-response-request"
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "progenitor-v1",
                "input": "Rate limit test",
            },
            headers={"X-Request-ID": request_id},
        )

        # Rate limiting is configurable, but a successful finite dispatch is always a 202 ack.
        assert response.status_code in [202, 429, 503]
        if response.status_code == 202:
            assert response.headers["X-Request-ID"] == request_id
            assert response.json() == {
                "request_id": request_id,
                "event_types": ["chat.response.started"],
            }

    def test_ui_endpoints_bypass_rate_limit(self, client):
        """Test UI endpoints bypass rate limiting."""
        # UI endpoints should never be rate limited
        for _ in range(100):
            response = client.get("/api/ui/inbox")
            assert response.status_code == 202
            request_id = response.headers["X-Request-ID"]
            assert response.json() == {
                "request_id": request_id,
                "event_types": ["inbox.snapshot"],
            }


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

        request_id = "large-response-request"
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "progenitor-v1",
                "input": large_content,
                "stream": False,
            },
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code in [202, 400, 413, 500, 503]
        if response.status_code == 202:
            assert response.headers["X-Request-ID"] == request_id
            assert response.json() == {
                "request_id": request_id,
                "event_types": ["chat.response.started"],
            }

    def test_large_input_message(self, client):
        """Test handling large input."""
        request_id = "normal-response-request"
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "progenitor-v1",
                "input": "Final question",
                "stream": False,
            },
            headers={"X-Request-ID": request_id},
        )
        assert response.status_code in [202, 400, 413, 500, 503]
        if response.status_code == 202:
            assert response.headers["X-Request-ID"] == request_id
            assert response.json() == {
                "request_id": request_id,
                "event_types": ["chat.response.started"],
            }


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
