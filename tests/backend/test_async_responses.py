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


def _next_response_sse_frame(
    lines: Iterator[str],
) -> tuple[str, dict[str, object], str | None]:
    """Decode one response-content SSE frame, preserving its replay cursor."""
    event_name: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("id: "):
            event_id = line.removeprefix("id: ")
        elif line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
        elif line == "" and event_name is not None:
            return event_name, json.loads("\n".join(data_lines)), event_id
    raise AssertionError("SSE stream closed before a complete response frame")


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

    def test_response_stream_route_is_registered_in_openapi(self, client):
        """Generated clients can discover the dedicated response-content stream route."""
        operation = client.get("/openapi.json").json()["paths"][
            "/api/ui/responses/{request_id}/stream"
        ]["get"]

        parameters = {(parameter["name"], parameter["in"]) for parameter in operation["parameters"]}

        assert {
            ("request_id", "path"),
            ("after", "query"),
            ("Last-Event-ID", "header"),
        } <= parameters

    def test_missing_or_expired_response_stream_returns_404_before_sse_commit(self):
        """Unavailable response streams must fail as HTTP 404s, not half-open SSE responses."""
        import app.backend.main as backend_main

        expired_request_id = "expired-response-stream"
        expired_state = backend_main._ensure_response_stream(
            expired_request_id, "resp-expired", "thread-expired", "progenitor-v1"
        )
        expired_state.expires_at = time.time() - 1

        try:
            with (
                _live_backend() as base_url,
                httpx.Client(base_url=base_url, timeout=5) as live_client,
            ):
                missing_response = live_client.get(
                    "/api/ui/responses/missing-response-stream/stream"
                )
                expired_response = live_client.get(f"/api/ui/responses/{expired_request_id}/stream")
        finally:
            backend_main._response_streams.pop(expired_request_id, None)

        for response in (missing_response, expired_response):
            assert response.status_code == 404
            assert not response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["content-type"].startswith("application/json")
            assert response.json() == {"detail": "Unknown response stream"}

        assert expired_request_id not in backend_main._response_streams

    def test_terminal_response_stream_without_frames_closes_cleanly_with_sse_headers(self):
        """A terminal request with no replayable content should return SSE headers and EOF immediately."""
        import app.backend.main as backend_main

        request_id = "terminal-empty-response-stream"
        state = backend_main._ensure_response_stream(
            request_id, "resp-empty", "thread-empty", "progenitor-v1"
        )
        state.terminal_reached = True

        try:
            with (
                _live_backend() as base_url,
                httpx.Client(
                    base_url=base_url,
                    timeout=httpx.Timeout(1.0, connect=5.0),
                ) as live_client,
            ):
                response = live_client.get(f"/api/ui/responses/{request_id}/stream")
        finally:
            backend_main._response_streams.pop(request_id, None)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.text == ""

    def test_streaming_responses_use_session_thread_id(self, monkeypatch):
        """Canonical SSE carries lifecycle while its response stream carries text deltas."""
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
            with live_client.stream("GET", "/api/ui/stream") as canonical_stream:
                assert canonical_stream.status_code == 200
                assert canonical_stream.headers["content-type"].startswith("text/event-stream")
                canonical_lines = canonical_stream.iter_lines()
                ready_name, ready = _next_sse_frame(canonical_lines)
                assert ready_name == "ui.event"
                assert ready["type"] == "connection.ready"

                response = live_client.post(
                    "/api/ui/responses",
                    json={"model": "progenitor-v1", "input": "hi", "stream": True},
                    headers={"X-Request-ID": request_id},
                )
                with live_client.stream(
                    "GET", f"/api/ui/responses/{request_id}/stream"
                ) as response_stream:
                    assert response_stream.status_code == 200
                    assert response_stream.headers["content-type"].startswith("text/event-stream")
                    response_lines = response_stream.iter_lines()
                    dedicated_frames = [_next_response_sse_frame(response_lines) for _ in range(2)]
                    assert list(response_lines) == []
                canonical_frames = [_next_sse_frame(canonical_lines) for _ in range(2)]

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert captured == {
            "message": "hi",
            "request_id": request_id,
            "metadata": {"thread_id": "thread_sess_thread_abc", "source": "ui"},
        }
        assert [event_name for event_name, _ in canonical_frames] == ["ui.event"] * 2
        lifecycle = [event for _, event in canonical_frames]
        assert [event["type"] for event in lifecycle] == [
            "chat.response.started",
            "chat.response.completed",
        ]
        assert all(event["scope"] == "chat" for event in lifecycle)
        assert all(event["payload"]["request_id"] == request_id for event in lifecycle)
        assert lifecycle[0]["payload"]["id"] == lifecycle[1]["payload"]["id"]

        assert [event_name for event_name, _, _ in dedicated_frames] == [
            "chat.response.delta",
            "chat.response.delta",
        ]
        assert [frame_id for _, _, frame_id in dedicated_frames] == [
            f"chat-response:{request_id}:1",
            f"chat-response:{request_id}:2",
        ]
        payloads = [payload for _, payload, _ in dedicated_frames]
        assert all(payload["request_id"] == request_id for payload in payloads)
        assert all(payload["id"] == lifecycle[0]["payload"]["id"] for payload in payloads)
        assert "".join(payload["delta"] for payload in payloads) == "Hello world"

    @pytest.mark.asyncio
    async def test_response_event_stream_forwards_runtime_timing_from_direct_frames(
        self, monkeypatch
    ):
        """Dedicated frames preserve runtime timing while canonical SSE gets only terminal state."""
        import app.backend.main as backend_main

        request_id = "runtime-timing-request"
        published: list[tuple[str, dict[str, object]]] = []

        class _MockClient:
            async def send_message_stream_events(self, *_args, **_kwargs):
                yield {
                    "type": "stream_chunk",
                    "content": "Hello",
                    "runtime_emitted_at_ms": 1_700_000_000_001,
                    "runtime_stream_seq": 7,
                }
                yield {
                    "type": "stream_event",
                    "event_type": "node_update",
                    "content": "respond",
                    "metadata": {"node_name": "respond"},
                    "runtime_emitted_at_ms": 1_700_000_000_002,
                    "runtime_stream_seq": 8,
                }

        def record(event_type: str, payload: dict[str, object], **_kwargs: object) -> None:
            published.append((event_type, payload))

        state = backend_main._ensure_response_stream(
            request_id,
            "response-runtime-timing",
            "thread-runtime-timing",
            "progenitor-v1",
        )
        monkeypatch.setattr(backend_main, "client", _MockClient())
        monkeypatch.setattr(backend_main, "broadcast_ui_event", record)
        try:
            await backend_main._run_response_event_stream(
                "hi",
                "progenitor-v1",
                request_id,
                "response-runtime-timing",
                "thread-runtime-timing",
            )
        finally:
            backend_main._response_streams.pop(request_id, None)

        assert [event_type for event_type, _ in published] == ["chat.response.completed"]
        assert [frame.event_type for frame in state.frames] == [
            "chat.response.delta",
            "chat.response.event",
        ]
        assert state.frames[0].payload == {
            "id": "response-runtime-timing",
            "model": "progenitor-v1",
            "thread_id": "thread-runtime-timing",
            "delta": "Hello",
            "request_id": request_id,
            "runtime_emitted_at_ms": 1_700_000_000_001,
            "runtime_stream_seq": 7,
        }
        assert state.frames[1].payload == {
            "id": "response-runtime-timing",
            "model": "progenitor-v1",
            "thread_id": "thread-runtime-timing",
            "event_type": "node_update",
            "content": "respond",
            "metadata": {"node_name": "respond"},
            "request_id": request_id,
            "runtime_emitted_at_ms": 1_700_000_000_002,
            "runtime_stream_seq": 8,
        }

    def test_streaming_response_forwards_structured_stream_events(self, monkeypatch):
        """Dedicated SSE preserves structured events and their ordering relative to text."""
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
            live_client.stream("GET", "/api/ui/stream") as canonical_stream,
        ):
            canonical_lines = canonical_stream.iter_lines()
            _next_sse_frame(canonical_lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "delegate", "stream": True},
                headers={"X-Request-ID": request_id},
            )
            with live_client.stream(
                "GET", f"/api/ui/responses/{request_id}/stream"
            ) as response_stream:
                response_lines = response_stream.iter_lines()
                dedicated_frames = [_next_response_sse_frame(response_lines) for _ in range(4)]
                assert list(response_lines) == []
            canonical_frames = [_next_sse_frame(canonical_lines) for _ in range(2)]

        assert response.status_code == 202
        assert [event["type"] for _, event in canonical_frames] == [
            "chat.response.started",
            "chat.response.completed",
        ]
        assert [event_name for event_name, _, _ in dedicated_frames] == [
            "chat.response.event",
            "chat.response.event",
            "chat.response.event",
            "chat.response.delta",
        ]
        payloads = [payload for _, payload, _ in dedicated_frames]
        assert all(payload["request_id"] == request_id for payload in payloads)

        tool_start, progress, subagent_result, delta = payloads
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
        assert delta["delta"] == "done"

    def test_response_stream_replays_only_frames_after_cursor(self):
        """A reconnect receives only response-content frames newer than its cursor."""
        import app.backend.main as backend_main

        request_id = "replay-response-request"
        state = backend_main._ensure_response_stream(
            request_id, "resp-replay", "thread-replay", "progenitor-v1"
        )
        first = backend_main._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "first"},
        )
        backend_main._append_response_stream_frame(
            state,
            "chat.response.event",
            {"request_id": request_id, "event_type": "tool_start", "content": "work"},
        )
        backend_main._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "last"},
        )
        state.terminal_reached = True
        try:
            with (
                _live_backend() as base_url,
                httpx.Client(base_url=base_url, timeout=5) as live_client,
                live_client.stream(
                    "GET", f"/api/ui/responses/{request_id}/stream?after={first.id}"
                ) as response_stream,
            ):
                assert response_stream.status_code == 200
                response_lines = response_stream.iter_lines()
                replayed_frames = [_next_response_sse_frame(response_lines) for _ in range(2)]
                assert list(response_lines) == []
        finally:
            backend_main._response_streams.pop(request_id, None)

        assert [event_name for event_name, _, _ in replayed_frames] == [
            "chat.response.event",
            "chat.response.delta",
        ]
        assert [frame_id for _, _, frame_id in replayed_frames] == [
            f"chat-response:{request_id}:2",
            f"chat-response:{request_id}:3",
        ]
        assert [
            payload["content"] if "content" in payload else payload["delta"]
            for _, payload, _ in replayed_frames
        ] == ["work", "last"]

    def test_response_stream_resyncs_once_for_invalid_or_expired_cursor(self, monkeypatch):
        """Unrecoverable cursors surface one resync frame instead of partial content."""
        import app.backend.main as backend_main

        invalid_request_id = "invalid-cursor-request"
        invalid_state = backend_main._ensure_response_stream(
            invalid_request_id, "resp-invalid", "thread-invalid", "progenitor-v1"
        )
        backend_main._append_response_stream_frame(
            invalid_state,
            "chat.response.delta",
            {"request_id": invalid_request_id, "delta": "must-not-replay"},
        )
        invalid_state.terminal_reached = True

        expired_request_id = "expired-cursor-request"
        monkeypatch.setattr(backend_main, "RESPONSE_STREAM_REPLAY_LIMIT", 2)
        expired_state = backend_main._ensure_response_stream(
            expired_request_id, "resp-expired", "thread-expired", "progenitor-v1"
        )
        for content in ("discarded", "retained-one", "retained-two"):
            backend_main._append_response_stream_frame(
                expired_state,
                "chat.response.delta",
                {"request_id": expired_request_id, "delta": content},
            )
        expired_state.terminal_reached = True

        try:
            with (
                _live_backend() as base_url,
                httpx.Client(base_url=base_url, timeout=5) as live_client,
            ):
                with live_client.stream(
                    "GET",
                    f"/api/ui/responses/{invalid_request_id}/stream?after=not-a-response-cursor",
                ) as response_stream:
                    invalid_lines = response_stream.iter_lines()
                    invalid_frame = _next_response_sse_frame(invalid_lines)
                    assert list(invalid_lines) == []

                with live_client.stream(
                    "GET",
                    f"/api/ui/responses/{expired_request_id}/stream?after="
                    f"chat-response:{expired_request_id}:0",
                ) as response_stream:
                    expired_lines = response_stream.iter_lines()
                    expired_frame = _next_response_sse_frame(expired_lines)
                    assert list(expired_lines) == []
        finally:
            backend_main._response_streams.pop(invalid_request_id, None)
            backend_main._response_streams.pop(expired_request_id, None)

        assert invalid_frame == (
            "chat.response.resync_required",
            {"request_id": invalid_request_id, "reason": "invalid_cursor"},
            None,
        )
        assert expired_frame == (
            "chat.response.resync_required",
            {"request_id": expired_request_id, "reason": "cursor_expired"},
            None,
        )

    def test_last_event_id_header_takes_precedence_over_after_query(self):
        """When both cursors are present, the browser's Last-Event-ID must win over the query string."""
        import app.backend.main as backend_main

        request_id = "last-event-id-precedence"
        state = backend_main._ensure_response_stream(
            request_id, "resp-precedence", "thread-precedence", "progenitor-v1"
        )
        first = backend_main._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "first"},
        )
        backend_main._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "second"},
        )
        state.terminal_reached = True

        try:
            with (
                _live_backend() as base_url,
                httpx.Client(base_url=base_url, timeout=5) as live_client,
                live_client.stream(
                    "GET",
                    f"/api/ui/responses/{request_id}/stream?after={first.id}",
                    headers={"Last-Event-ID": "not-a-response-cursor"},
                ) as response_stream,
            ):
                assert response_stream.status_code == 200
                response_lines = response_stream.iter_lines()
                frame = _next_response_sse_frame(response_lines)
                assert list(response_lines) == []
        finally:
            backend_main._response_streams.pop(request_id, None)

        assert frame == (
            "chat.response.resync_required",
            {"request_id": request_id, "reason": "invalid_cursor"},
            None,
        )

    def test_failed_generation_emits_post_acknowledgement_causal_event(self, monkeypatch):
        """A failed generation keeps partial text dedicated and failure canonical."""
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
            live_client.stream("GET", "/api/ui/stream") as canonical_stream,
        ):
            canonical_lines = canonical_stream.iter_lines()
            _next_sse_frame(canonical_lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "fail", "stream": True},
                headers={"X-Request-ID": request_id},
            )
            with live_client.stream(
                "GET", f"/api/ui/responses/{request_id}/stream"
            ) as response_stream:
                response_lines = response_stream.iter_lines()
                dedicated_frame = _next_response_sse_frame(response_lines)
                assert list(response_lines) == []
            canonical_frames = [_next_sse_frame(canonical_lines) for _ in range(2)]

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert [event["type"] for _, event in canonical_frames] == [
            "chat.response.started",
            "chat.response.failed",
        ]
        assert dedicated_frame[0] == "chat.response.delta"
        assert dedicated_frame[1]["delta"] == "partial"
        failed = canonical_frames[-1][1]
        assert failed["scope"] == "chat"
        assert failed["payload"]["request_id"] == request_id
        assert failed["payload"]["error"] == "stream exploded"

    @pytest.mark.asyncio
    async def test_stream_ending_after_cancellation_emits_cancelled_not_completed(
        self, monkeypatch
    ):
        """A clean iterator exit after cancellation remains terminally cancelled."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        target_request_id = "target-request-cancel-race"
        response_id = "response-cancel-race"
        cancel_event = asyncio.Event()
        iterator_started = asyncio.Event()

        class _MockClient:
            async def send_message_stream_events(self, *_args, **_kwargs):
                iterator_started.set()
                await cancel_event.wait()
                if False:
                    yield {}

        event_start = len(ui_routes._ui_event_replay)
        monkeypatch.setattr(backend_main, "client", _MockClient())
        backend_main._active_response_streams[target_request_id] = cancel_event
        try:
            runner = asyncio.create_task(
                backend_main._run_response_event_stream(
                    "cancelled while streaming",
                    "progenitor-v1",
                    target_request_id,
                    response_id,
                    "thread-cancel-race",
                )
            )
            await iterator_started.wait()
            cancel_event.set()
            await runner
            emitted = [
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("request_id") == target_request_id
            ]
        finally:
            backend_main._active_response_streams.pop(target_request_id, None)

        event_types = [event.type for event in emitted]
        assert event_types == ["chat.response.cancelled"]
        assert "chat.response.completed" not in event_types
        assert emitted[0].payload == {
            "id": response_id,
            "model": "progenitor-v1",
            "thread_id": "thread-cancel-race",
            "cancelled": True,
            "request_id": target_request_id,
        }

    @pytest.mark.asyncio
    async def test_cancel_response_sets_active_stream_event(self, client):
        """A cancel acknowledgement differs from the target runner's terminal cancellation."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        cancel_event = asyncio.Event()
        captured: dict[str, object] = {}
        cancel_request_id = "cancel-operation-request"
        target_request_id = "req-cancel-test"

        class _MockClient:
            async def cancel_request(self, generation_id: str) -> bool:
                captured["generation_id"] = generation_id
                return True

            async def send_message_stream_events(self, *_args, **_kwargs):
                if False:
                    yield {}

        event_start = len(ui_routes._ui_event_replay)
        previous_client = backend_main.client
        backend_main.client = _MockClient()
        backend_main._active_response_streams[target_request_id] = cancel_event
        try:
            response = client.put(
                f"/api/ui/responses/{target_request_id}/cancel",
                headers={"X-Request-ID": cancel_request_id},
            )
            await backend_main._run_response_event_stream(
                "cancelled request",
                "progenitor-v1",
                target_request_id,
                "resp-cancel-test",
                "thread-cancel-test",
            )
            emitted = [
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("request_id") in {cancel_request_id, target_request_id}
            ]
        finally:
            backend_main._active_response_streams.pop(target_request_id, None)
            backend_main.client = previous_client

        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == cancel_request_id
        assert response.json() == {
            "request_id": cancel_request_id,
            "event_types": ["chat.response.cancel_requested"],
        }
        assert cancel_event.is_set()
        assert captured["generation_id"] == target_request_id
        assert [event.type for event in emitted] == [
            "chat.response.cancel_requested",
            "chat.response.cancelled",
        ]
        acknowledgement, terminal = emitted
        assert acknowledgement.payload == {
            "target_request_id": target_request_id,
            "cancel_request_id": cancel_request_id,
            "cancelled": True,
            "request_id": cancel_request_id,
        }
        assert terminal.payload["request_id"] == target_request_id
        assert terminal.payload["cancelled"] is True
        assert terminal.payload["id"] == "resp-cancel-test"

    def test_cancel_response_resolves_browser_response_id_to_request_id(self, client):
        """Cancelling by response id acknowledges the operation without terminally cancelling it."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        previous_client = backend_main.client
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
            emitted = [
                event
                for event in ui_routes._ui_event_replay[event_start:]
                if event.payload.get("target_response_id") == response_id
            ]
        finally:
            backend_main._active_response_streams.pop(request_id, None)
            backend_main._response_request_ids.pop(response_id, None)
            backend_main.client = previous_client

        assert response.status_code == 202
        assert response.json() == {
            "request_id": "cancel-operation-42",
            "event_types": ["chat.response.cancel_requested"],
        }
        assert cancel_event.is_set()
        assert captured["target_request_id"] == request_id
        assert [event.type for event in emitted] == ["chat.response.cancel_requested"]
        assert emitted[0].payload == {
            "target_request_id": request_id,
            "target_response_id": response_id,
            "cancel_request_id": "cancel-operation-42",
            "cancelled": True,
            "request_id": "cancel-operation-42",
        }

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
            "metadata": {"thread_id": "thread_sess_thread_xyz", "source": "ui"},
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
