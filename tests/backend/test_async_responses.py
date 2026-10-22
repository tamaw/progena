"""Request lifecycle and dedicated SSE response-stream regressions."""

import asyncio
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

import src.backend.chat.responses as chat_responses
import src.backend.events.stream as events_stream
import src.backend.http.middleware as http_middleware
import src.backend.lifecycle.state as lifecycle_state
from src.backend.chat.streaming import reset_stop_flag
from src.backend.main import app


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

    events_stream._sse_shutdown_event = None
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
        events_stream._sse_shutdown_event = None
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

        event_start = len(events_stream._ui_event_replay)
        monkeypatch.setattr(lifecycle_state, "client", None)
        response = client.post(
            "/api/ui/responses",
            json={"model": "progena-v1", "input": "Hello", "stream": False},
            headers={"X-Request-ID": "rejected-generation"},
        )

        assert response.status_code == 503
        assert not any(
            event.payload.get("request_id") == "rejected-generation"
            for event in events_stream._ui_event_replay[event_start:]
        )

    def test_missing_or_expired_response_stream_returns_404_before_sse_commit(self):
        """Unavailable response streams must fail as HTTP 404s, not half-open SSE responses."""

        expired_request_id = "expired-response-stream"
        expired_state = chat_responses._ensure_response_stream(
            expired_request_id, "resp-expired", "thread-expired", "progena-v1"
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
            chat_responses._response_streams.pop(expired_request_id, None)

        for response in (missing_response, expired_response):
            assert response.status_code == 404
            assert not response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["content-type"].startswith("application/json")
            assert response.json() == {"detail": "Unknown response stream"}

        assert expired_request_id not in chat_responses._response_streams

    def test_terminal_response_stream_without_frames_closes_cleanly_with_sse_headers(self):
        """A terminal request with no replayable content should return SSE headers and EOF immediately."""

        request_id = "terminal-empty-response-stream"
        state = chat_responses._ensure_response_stream(
            request_id, "resp-empty", "thread-empty", "progena-v1"
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
            chat_responses._response_streams.pop(request_id, None)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["connection"] == "keep-alive"
        assert response.text == ""

    def test_streaming_responses_use_session_thread_id(self, monkeypatch):
        """Canonical SSE carries lifecycle while its response stream carries text deltas."""

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

        monkeypatch.setattr(lifecycle_state, "client", _MockClient())
        with _live_backend() as base_url, httpx.Client(base_url=base_url, timeout=5) as live_client:
            live_client.cookies.set("progena_session", "sess_thread_abc")
            with live_client.stream("GET", "/api/ui/stream") as canonical_stream:
                assert canonical_stream.status_code == 200
                assert canonical_stream.headers["content-type"].startswith("text/event-stream")
                canonical_lines = canonical_stream.iter_lines()
                ready_name, ready = _next_sse_frame(canonical_lines)
                assert ready_name == "ui.event"
                assert ready["type"] == "connection.ready"

                response = live_client.post(
                    "/api/ui/responses",
                    json={"model": "progena-v1", "input": "hi", "stream": True},
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

        state = chat_responses._ensure_response_stream(
            request_id,
            "response-runtime-timing",
            "thread-runtime-timing",
            "progena-v1",
        )
        monkeypatch.setattr(lifecycle_state, "client", _MockClient())
        monkeypatch.setattr(events_stream, "broadcast_ui_event", record)
        try:
            await chat_responses._run_response_event_stream(
                "hi",
                "progena-v1",
                request_id,
                "response-runtime-timing",
                "thread-runtime-timing",
            )
        finally:
            chat_responses._response_streams.pop(request_id, None)

        assert [event_type for event_type, _ in published] == ["chat.response.completed"]
        assert [frame.event_type for frame in state.frames] == [
            "chat.response.delta",
            "chat.response.event",
        ]
        assert state.frames[0].payload == {
            "id": "response-runtime-timing",
            "model": "progena-v1",
            "thread_id": "thread-runtime-timing",
            "delta": "Hello",
            "request_id": request_id,
            "runtime_emitted_at_ms": 1_700_000_000_001,
            "runtime_stream_seq": 7,
        }
        assert state.frames[1].payload == {
            "id": "response-runtime-timing",
            "model": "progena-v1",
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

        monkeypatch.setattr(lifecycle_state, "client", _MockClient())
        with (
            _live_backend() as base_url,
            httpx.Client(base_url=base_url, timeout=5) as live_client,
            live_client.stream("GET", "/api/ui/stream") as canonical_stream,
        ):
            canonical_lines = canonical_stream.iter_lines()
            _next_sse_frame(canonical_lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progena-v1", "input": "delegate", "stream": True},
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

        request_id = "replay-response-request"
        state = chat_responses._ensure_response_stream(
            request_id, "resp-replay", "thread-replay", "progena-v1"
        )
        first = chat_responses._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "first"},
        )
        chat_responses._append_response_stream_frame(
            state,
            "chat.response.event",
            {"request_id": request_id, "event_type": "tool_start", "content": "work"},
        )
        chat_responses._append_response_stream_frame(
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
            chat_responses._response_streams.pop(request_id, None)

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

        invalid_request_id = "invalid-cursor-request"
        invalid_state = chat_responses._ensure_response_stream(
            invalid_request_id, "resp-invalid", "thread-invalid", "progena-v1"
        )
        chat_responses._append_response_stream_frame(
            invalid_state,
            "chat.response.delta",
            {"request_id": invalid_request_id, "delta": "must-not-replay"},
        )
        invalid_state.terminal_reached = True

        expired_request_id = "expired-cursor-request"
        monkeypatch.setattr(chat_responses, "RESPONSE_STREAM_REPLAY_LIMIT", 2)
        expired_state = chat_responses._ensure_response_stream(
            expired_request_id, "resp-expired", "thread-expired", "progena-v1"
        )
        for content in ("discarded", "retained-one", "retained-two"):
            chat_responses._append_response_stream_frame(
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
            chat_responses._response_streams.pop(invalid_request_id, None)
            chat_responses._response_streams.pop(expired_request_id, None)

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

        request_id = "last-event-id-precedence"
        state = chat_responses._ensure_response_stream(
            request_id, "resp-precedence", "thread-precedence", "progena-v1"
        )
        first = chat_responses._append_response_stream_frame(
            state,
            "chat.response.delta",
            {"request_id": request_id, "delta": "first"},
        )
        chat_responses._append_response_stream_frame(
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
            chat_responses._response_streams.pop(request_id, None)

        assert frame == (
            "chat.response.resync_required",
            {"request_id": request_id, "reason": "invalid_cursor"},
            None,
        )

    def test_failed_generation_emits_post_acknowledgement_causal_event(self, monkeypatch):
        """A failed generation keeps partial text dedicated and failure canonical."""

        request_id = "e234a82e-00c1-4a96-bebe-652485775d92"

        class _FailingClient:
            async def send_message_stream_events(self, *_args, **_kwargs):
                yield {"type": "stream_chunk", "content": "partial"}
                raise RuntimeError("stream exploded")

        monkeypatch.setattr(lifecycle_state, "client", _FailingClient())
        with (
            _live_backend() as base_url,
            httpx.Client(base_url=base_url, timeout=5) as live_client,
            live_client.stream("GET", "/api/ui/stream") as canonical_stream,
        ):
            canonical_lines = canonical_stream.iter_lines()
            _next_sse_frame(canonical_lines)
            response = live_client.post(
                "/api/ui/responses",
                json={"model": "progena-v1", "input": "fail", "stream": True},
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

        event_start = len(events_stream._ui_event_replay)
        monkeypatch.setattr(lifecycle_state, "client", _MockClient())
        chat_responses._active_response_streams[target_request_id] = cancel_event
        try:
            runner = asyncio.create_task(
                chat_responses._run_response_event_stream(
                    "cancelled while streaming",
                    "progena-v1",
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
                for event in events_stream._ui_event_replay[event_start:]
                if event.payload.get("request_id") == target_request_id
            ]
        finally:
            chat_responses._active_response_streams.pop(target_request_id, None)

        event_types = [event.type for event in emitted]
        assert event_types == ["chat.response.cancelled"]
        assert "chat.response.completed" not in event_types
        assert emitted[0].payload == {
            "id": response_id,
            "model": "progena-v1",
            "thread_id": "thread-cancel-race",
            "cancelled": True,
            "request_id": target_request_id,
        }

    @pytest.mark.asyncio
    async def test_cancel_response_sets_active_stream_event(self, client):
        """A cancel acknowledgement differs from the target runner's terminal cancellation."""

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

        event_start = len(events_stream._ui_event_replay)
        previous_client = lifecycle_state.client
        lifecycle_state.client = _MockClient()
        chat_responses._active_response_streams[target_request_id] = cancel_event
        try:
            response = client.put(
                f"/api/ui/responses/{target_request_id}/cancel",
                headers={"X-Request-ID": cancel_request_id},
            )
            await chat_responses._run_response_event_stream(
                "cancelled request",
                "progena-v1",
                target_request_id,
                "resp-cancel-test",
                "thread-cancel-test",
            )
            emitted = [
                event
                for event in events_stream._ui_event_replay[event_start:]
                if event.payload.get("request_id") in {cancel_request_id, target_request_id}
            ]
        finally:
            chat_responses._active_response_streams.pop(target_request_id, None)
            lifecycle_state.client = previous_client

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

        previous_client = lifecycle_state.client
        request_id = "generation-request-42"
        response_id = "resp-browser-42"
        cancel_event = asyncio.Event()
        captured: dict[str, object] = {}

        class _MockClient:
            async def cancel_request(self, target_request_id: str) -> bool:
                captured["target_request_id"] = target_request_id
                return True

        lifecycle_state.client = _MockClient()
        chat_responses._active_response_streams[request_id] = cancel_event
        chat_responses._response_request_ids[response_id] = request_id
        event_start = len(events_stream._ui_event_replay)
        try:
            response = client.put(
                f"/api/ui/responses/{response_id}/cancel",
                headers={"X-Request-ID": "cancel-operation-42"},
            )
            emitted = [
                event
                for event in events_stream._ui_event_replay[event_start:]
                if event.payload.get("target_response_id") == response_id
            ]
        finally:
            chat_responses._active_response_streams.pop(request_id, None)
            chat_responses._response_request_ids.pop(response_id, None)
            lifecycle_state.client = previous_client

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

        class _RejectingLimiter:
            def is_allowed(self, _client_id: str):
                raise AssertionError("healthz must not consult the API rate limiter")

        monkeypatch.setattr(http_middleware, "rate_limiter", _RejectingLimiter())
        monkeypatch.setattr(lifecycle_state.config, "rate_limit_enabled", True)

        response = client.get("/healthz")

        assert response.status_code == 200
        assert response.content == b""

    def test_cancel_response_returns_404_for_unknown_request(self, client):
        """Unknown cancellation IDs should not be reported as successful."""

        original_client = lifecycle_state.client

        class _MockClient:
            async def cancel_request(self, request_id: str) -> bool:
                del request_id
                return False

        lifecycle_state.client = _MockClient()

        try:
            response = client.put(
                "/api/ui/responses/not-active/cancel",
                headers={"X-Request-ID": "unknown-cancel-request"},
            )
        finally:
            lifecycle_state.client = original_client

        assert response.status_code == 404

    def test_non_streaming_responses_use_session_thread_id(self, client):
        """Non-streaming output is event data, never a finite HTTP response body."""

        original_client = lifecycle_state.client
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
                from src.backend.chat.models import AgentResponse

                return AgentResponse(response="Hello", reasoning=None, reasoning_tokens=None)

        event_start = len(events_stream._ui_event_replay)
        lifecycle_state.client = _MockClient()
        try:
            client.cookies.set("progena_session", "sess_thread_xyz")
            response = client.post(
                "/api/ui/responses",
                json={"model": "progena-v1", "input": "hi", "stream": False},
                headers={"X-Request-ID": request_id},
            )
            events = [
                event
                for event in events_stream._ui_event_replay[event_start:]
                if event.payload.get("request_id") == request_id
            ]
        finally:
            client.cookies.clear()
            lifecycle_state.client = original_client

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

        original_client = lifecycle_state.client

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
                from src.backend.chat.models import AgentResponse

                return AgentResponse(
                    response="10.50. At $0.75 per pencil, 14 pencils cost $10.50.",
                    reasoning="Checked the unit price before multiplying by 14.",
                    reasoning_tokens=17,
                )

        event_start = len(events_stream._ui_event_replay)
        lifecycle_state.client = _MockClient()

        request_id = "reasoning-generation-request"
        try:
            response = client.post(
                "/api/ui/responses",
                json={
                    "model": "progena-v1",
                    "input": "hi",
                    "stream": False,
                    "include_reasoning": True,
                },
                headers={"X-Request-ID": request_id},
            )
            events = [
                event
                for event in events_stream._ui_event_replay[event_start:]
                if event.payload.get("request_id") == request_id
            ]
        finally:
            lifecycle_state.client = original_client

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
