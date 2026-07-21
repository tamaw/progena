"""Contract tests for the HTTP + SSE chat endpoints (RuntimeChatService).

These port the behaviors of the old TCP IPC server's direct-DeepAgent path
(``app/progenitor/ipc/server.py``) onto the new HTTP transport.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from aiohttp import web

from app.progenitor.config import RuntimeConfig
from app.progenitor.runtime.http_chat import RuntimeChatService
from app.progenitor.runtime.queues import RuntimeQueues

pytestmark = pytest.mark.asyncio


# -- Test doubles -------------------------------------------------------


class _Wait:
    """Script item: suspend the bridge until ``event`` is set."""

    def __init__(self, event: asyncio.Event) -> None:
        self.event = event


class _Raise:
    """Script item: raise ``exc`` from the bridge's stream."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class ScriptedBridge:
    """Fake DeepAgentIPCStream bridge that replays a scripted item list."""

    def __init__(
        self,
        graph: Any,
        *,
        config: Any,
        state: dict[str, Any],
        script: Any,
    ) -> None:
        self.graph = graph
        self.config = config
        self.state = state
        self._script = script
        self.thread_id: str | None = None

    async def stream(self, message: str, *, thread_id: str, received_at: float | None = None):
        self.thread_id = thread_id
        items = self._script(message) if callable(self._script) else self._script
        for item in items:
            if isinstance(item, _Wait):
                await item.event.wait()
                continue
            if isinstance(item, _Raise):
                raise item.exc
            yield item


def make_bridge_factory(script: Any) -> Callable[..., ScriptedBridge]:
    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        return ScriptedBridge(
            graph,
            config=config,
            state=state,
            script=script,
        )

    return factory


class FakeStateManager:
    def __init__(self) -> None:
        self.saved_checkpoints: list[dict[str, Any]] = []

    async def save_current_state(self, checkpoint: dict[str, Any]) -> None:
        self.saved_checkpoints.append(checkpoint)


@dataclass
class FakeHeartbeatContext:
    state_manager: FakeStateManager
    audit_log_sink: Any = None


@dataclass
class FakeLoopRunner:
    graph: Any = field(default_factory=lambda: SimpleNamespace(astream=lambda *a, **k: None))
    thread_id: str = "progenitor_main"

    def __post_init__(self) -> None:
        self.graph_lock = asyncio.Lock()
        self.config = SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=False))
        self.state: dict[str, Any] = {"thread_id": self.thread_id, "cycle_count": 0}
        self.state_manager = FakeStateManager()
        self.heartbeat_context = FakeHeartbeatContext(self.state_manager)
        self.queues = RuntimeQueues(RuntimeConfig())


@dataclass
class _ChatServer:
    runner: web.AppRunner
    service: RuntimeChatService
    base_url: str


async def _start_chat_server(
    loop_runner: Any, *, bridge_factory: Any = None, start_drain: bool = False
) -> _ChatServer:
    """Start a standalone chat server.

    ``start_drain`` launches the ``/v1/events`` fan-out loop, which is also the
    sole consumer of ``loop_runner.queues.get_event()``. Leave it off (default)
    for chat-only tests that assert directly against the event queue, since a
    running drain loop would race them for the same single-consumer queue.
    """
    service = RuntimeChatService(loop_runner, bridge_factory=bridge_factory)
    if start_drain:
        await service.start()
    app = web.Application()
    app.add_routes(service.routes())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, reuse_address=True)
    await site.start()
    server = getattr(site, "_server", None)
    sockets = getattr(server, "sockets", None)
    port = int(sockets[0].getsockname()[1]) if sockets else 0
    return _ChatServer(runner=runner, service=service, base_url=f"http://127.0.0.1:{port}")


async def _stop_chat_server(server: _ChatServer) -> None:
    await server.service.stop()
    await server.runner.cleanup()


async def _post_chat(
    client: httpx.AsyncClient,
    base_url: str,
    message: str,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    body: dict[str, Any] = {"message": message, "request_id": request_id}
    if metadata is not None:
        body["metadata"] = metadata
    async with client.stream("POST", f"{base_url}/v1/chat", json=body) as response:
        assert response.status_code == 200
        event_name: str | None = None
        async for line in response.aiter_lines():
            if line == "" or line.startswith(":"):
                continue
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
                continue
            if line.startswith("data: "):
                assert event_name is not None
                data = json.loads(line[len("data: ") :])
                frames.append((event_name, data))
                if event_name in ("done", "error"):
                    break
    return frames


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))


# -- Tests ----------------------------------------------------------------


async def test_chat_stream_envelope_order_and_done() -> None:
    loop_runner = FakeLoopRunner()
    script = [
        {
            "type": "stream_event",
            "event_type": "node_update",
            "content": "",
            "metadata": {"node_name": "respond"},
        },
        {"type": "stream_chunk", "content": "Hello", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_chat(client, server.base_url, "hi", "req-1")
    finally:
        await _stop_chat_server(server)

    stream_payloads = [
        payload for event_name, payload in frames if event_name in {"stream_event", "stream_chunk"}
    ]
    assert [payload["runtime_stream_seq"] for payload in stream_payloads] == list(
        range(1, len(stream_payloads) + 1)
    )
    assert all(
        type(payload["runtime_emitted_at_ms"]) is int and payload["runtime_emitted_at_ms"] > 0
        for payload in stream_payloads
    )
    assert [
        (
            event_name,
            {
                field: value
                for field, value in payload.items()
                if field not in {"runtime_emitted_at_ms", "runtime_stream_seq"}
            },
        )
        for event_name, payload in frames
    ] == [
        (
            "stream_event",
            {
                "type": "stream_event",
                "event_type": "node_update",
                "content": "",
                "metadata": {"node_name": "respond"},
                "request_id": "req-1",
            },
        ),
        (
            "stream_chunk",
            {"type": "stream_chunk", "content": "Hello", "is_final": False, "request_id": "req-1"},
        ),
        (
            "stream_chunk",
            {"type": "stream_chunk", "content": "", "is_final": True, "request_id": "req-1"},
        ),
        ("done", {"request_id": "req-1", "status": "completed"}),
    ]


async def test_chat_rejects_missing_message_and_bad_json() -> None:
    loop_runner = FakeLoopRunner()
    server = await _start_chat_server(loop_runner)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server.base_url}/v1/chat", json={})
            assert resp.status_code == 400
            assert resp.json()["code"] == "BAD_REQUEST"

            resp2 = await client.post(
                f"{server.base_url}/v1/chat",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp2.status_code == 400
            assert resp2.json()["code"] == "BAD_REQUEST"
    finally:
        await _stop_chat_server(server)


async def test_chat_503_when_graph_missing_astream() -> None:
    loop_runner = FakeLoopRunner(graph=object())
    server = await _start_chat_server(loop_runner)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server.base_url}/v1/chat", json={"message": "hi"})
            assert resp.status_code == 503
            assert resp.json()["code"] == "GRAPH_UNAVAILABLE"
    finally:
        await _stop_chat_server(server)


async def test_chat_serializes_requests_and_emits_queued_event() -> None:
    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = {
        "first": [_Wait(gate), {"type": "stream_chunk", "content": "first-done", "is_final": True}],
        "second": [{"type": "stream_chunk", "content": "second-done", "is_final": True}],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda msg: script[msg])
    )
    task_a = task_b = None
    try:
        async with _client() as client:
            task_a = asyncio.create_task(_post_chat(client, server.base_url, "first", "req-a"))
            await asyncio.sleep(0.2)
            task_b = asyncio.create_task(_post_chat(client, server.base_url, "second", "req-b"))
            await asyncio.sleep(0.2)

            queued_event = await loop_runner.queues.get_event(timeout=1.0)
            assert queued_event is not None
            assert queued_event["event_type"] == "message_queued"
            assert queued_event["metadata"]["request_id"] == "req-b"

            gate.set()
            frames_a = await asyncio.wait_for(task_a, timeout=5.0)
            frames_b = await asyncio.wait_for(task_b, timeout=5.0)
    finally:
        gate.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
        await _stop_chat_server(server)

    assert frames_a[-1] == ("done", {"request_id": "req-a", "status": "completed"})
    assert frames_b[0] == ("queued", {"request_id": "req-b", "streaming": True})
    assert frames_b[-1] == ("done", {"request_id": "req-b", "status": "completed"})
    assert [
        [
            payload["runtime_stream_seq"]
            for event_name, payload in request_frames
            if event_name in {"stream_event", "stream_chunk"}
        ]
        for request_frames in (frames_a, frames_b)
    ] == [[1], [1]]


async def test_chat_cancel_mid_stream_skips_checkpoint() -> None:
    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = [
        _Wait(gate),
        {"type": "stream_chunk", "content": "should-not-arrive", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    task = None
    try:
        async with _client() as client:
            task = asyncio.create_task(_post_chat(client, server.base_url, "hi", "req-cancel"))
            await asyncio.sleep(0.2)

            cancel_resp = await client.post(f"{server.base_url}/v1/chat/req-cancel/cancel")
            assert cancel_resp.json() == {"request_id": "req-cancel", "cancelled": True}

            unknown_resp = await client.post(f"{server.base_url}/v1/chat/does-not-exist/cancel")
            assert unknown_resp.json() == {"request_id": "does-not-exist", "cancelled": False}

            gate.set()
            frames = await asyncio.wait_for(task, timeout=5.0)
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
        await _stop_chat_server(server)

    assert frames == [("done", {"request_id": "req-cancel", "status": "cancelled"})]
    assert loop_runner.state_manager.saved_checkpoints == []
    assert not loop_runner.queues.cycle_complete_event.is_set()


async def test_chat_bridge_cancellation_does_not_synthesize_done_frame() -> None:
    loop_runner = FakeLoopRunner()
    script = [
        {"type": "stream_chunk", "content": "partial", "is_final": False},
        _Raise(asyncio.CancelledError()),
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_chat(client, server.base_url, "hi", "req-bridge-cancel")
    finally:
        await _stop_chat_server(server)

    assert frames[0][0] == "stream_chunk"
    assert frames[0][1]["content"] == "partial"
    assert not any(name == "done" for name, _ in frames)
    assert loop_runner.state_manager.saved_checkpoints == []
    assert not loop_runner.queues.cycle_complete_event.is_set()


async def test_chat_writes_keepalive_comments_during_a_silent_bridge_gap(monkeypatch) -> None:
    """A long silent gap in bridge.stream() must not starve the response body.

    Regression test: bridge.stream() can go quiet for minutes (a long tool
    call or subagent invocation with no interim output). Without periodic
    keepalive bytes, the backend's httpx client eventually hits its read
    timeout and surfaces a spurious "Streaming error" to the user even though
    the turn was still in progress.
    """
    monkeypatch.setattr("app.progenitor.runtime.http_chat.EVENTS_KEEPALIVE_SECONDS", 0.05)

    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = [
        _Wait(gate),
        {"type": "stream_chunk", "content": "hello", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with (
            _client() as client,
            client.stream(
                "POST",
                f"{server.base_url}/v1/chat",
                json={"message": "hi", "request_id": "req-keepalive"},
            ) as response,
        ):
            assert response.status_code == 200
            raw = b""
            gate_released = False
            async for chunk in response.aiter_bytes():
                raw += chunk
                if not gate_released and raw.count(b": keepalive\n\n") >= 2:
                    gate_released = True
                    gate.set()
                if b"event: done\n" in raw:
                    break
    finally:
        gate.set()
        await _stop_chat_server(server)

    assert raw.count(b": keepalive\n\n") >= 2


async def test_direct_stream_queue_applies_backpressure(monkeypatch) -> None:
    """A slow response writer must bound how far the bridge producer gets ahead."""
    monkeypatch.setattr("app.progenitor.runtime.http_chat.DIRECT_STREAM_QUEUE_SIZE", 2)

    loop_runner = FakeLoopRunner()
    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    produced = 0

    class _FloodBridge:
        async def stream(self, _message: str, *, thread_id: str, received_at: float | None = None):
            del thread_id, received_at
            nonlocal produced
            for index in range(30):
                produced += 1
                yield {"type": "stream_chunk", "content": str(index), "is_final": False}
            yield {"type": "stream_chunk", "content": "done", "is_final": True}

    class _SlowResponse:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        async def write(self, payload: bytes) -> None:
            self.writes.append(payload)
            if len(self.writes) == 1:
                first_write_started.set()
                await release_first_write.wait()

    service = RuntimeChatService(
        loop_runner,
        bridge_factory=lambda *_args, **_kwargs: _FloodBridge(),
    )
    response = _SlowResponse()
    stream_task = asyncio.create_task(
        service._stream_chat(response, "hello", "request-queue-bound", None, asyncio.Event(), 123.0)
    )
    try:
        await asyncio.wait_for(first_write_started.wait(), timeout=2.0)
        await asyncio.sleep(0)
        # One item is being written and at most two are buffered; the producer
        # may have fetched the next item before blocking on queue.put().
        assert produced <= 4
        assert produced > 1
    finally:
        release_first_write.set()
        await asyncio.wait_for(stream_task, timeout=2.0)


async def test_chat_happy_path_runs_checkpoint_and_signals_cycle_complete() -> None:
    loop_runner = FakeLoopRunner()
    script = [{"type": "stream_chunk", "content": "ok", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_chat(client, server.base_url, "hi", "req-checkpoint")
    finally:
        await _stop_chat_server(server)

    assert frames[-1] == ("done", {"request_id": "req-checkpoint", "status": "completed"})
    assert len(loop_runner.state_manager.saved_checkpoints) == 1
    assert loop_runner.queues.cycle_complete_event.is_set()


async def test_chat_checkpoint_failure_emits_error_not_done() -> None:
    """A checkpoint failure must not be masked by an already-sent 'completed' frame."""
    loop_runner = FakeLoopRunner()

    async def failing_save_current_state(checkpoint: dict[str, Any]) -> None:
        raise RuntimeError("disk full")

    loop_runner.state_manager.save_current_state = failing_save_current_state  # type: ignore[method-assign]

    script = [{"type": "stream_chunk", "content": "ok", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_chat(client, server.base_url, "hi", "req-checkpoint-fail")
    finally:
        await _stop_chat_server(server)

    assert frames[-1][0] == "error"
    assert frames[-1][1]["code"] == "CHECKPOINT_ERROR"
    assert "disk full" in frames[-1][1]["error"]
    assert not any(name == "done" for name, _ in frames)


async def test_chat_bridge_error_emits_error_frame_and_releases_lock() -> None:
    loop_runner = FakeLoopRunner()
    script = {
        "boom": [_Raise(RuntimeError("kaboom"))],
        "ok": [{"type": "stream_chunk", "content": "fine", "is_final": True}],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda msg: script[msg])
    )
    try:
        async with _client() as client:
            frames = await _post_chat(client, server.base_url, "boom", "req-err")
            assert frames[-1][0] == "error"
            assert frames[-1][1]["code"] == "STREAM_ERROR"
            assert "kaboom" in frames[-1][1]["error"]

            frames_ok = await _post_chat(client, server.base_url, "ok", "req-ok")
            assert frames_ok[-1] == ("done", {"request_id": "req-ok", "status": "completed"})
    finally:
        await _stop_chat_server(server)


async def test_chat_thread_id_resolution_precedence() -> None:
    loop_runner = FakeLoopRunner(thread_id="state-thread")
    captured: list[ScriptedBridge] = []

    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        bridge = ScriptedBridge(
            graph,
            config=config,
            state=state,
            script=lambda _msg: [{"type": "stream_chunk", "content": "x", "is_final": True}],
        )
        captured.append(bridge)
        return bridge

    server = await _start_chat_server(loop_runner, bridge_factory=factory)
    try:
        async with _client() as client:
            await _post_chat(client, server.base_url, "hi", "req-a")
            assert captured[-1].thread_id == "state-thread"

            await _post_chat(
                client, server.base_url, "hi", "req-b", metadata={"thread_id": "explicit-thread"}
            )
            assert captured[-1].thread_id == "explicit-thread"
    finally:
        await _stop_chat_server(server)


async def test_events_broadcast_to_multiple_subscribers() -> None:
    loop_runner = FakeLoopRunner()
    server = await _start_chat_server(loop_runner, start_drain=True)
    try:
        async with _client() as client:

            async def _read_one_event(c: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
                async with c.stream("GET", f"{server.base_url}/v1/events") as resp:
                    assert resp.status_code == 200
                    event_name: str | None = None
                    async for line in resp.aiter_lines():
                        if line == "" or line.startswith(":"):
                            continue
                        if line.startswith("event: "):
                            event_name = line[len("event: ") :]
                            continue
                        if line.startswith("data: "):
                            assert event_name is not None
                            return event_name, json.loads(line[len("data: ") :])
                raise AssertionError("stream ended without an event")

            task_a = asyncio.create_task(_read_one_event(client))
            task_b = asyncio.create_task(_read_one_event(client))
            await asyncio.sleep(0.2)
            await loop_runner.queues.push_event("tick", "hello", {"n": 1})

            result_a = await asyncio.wait_for(task_a, timeout=5.0)
            result_b = await asyncio.wait_for(task_b, timeout=5.0)
    finally:
        await _stop_chat_server(server)

    assert result_a[0] == "agent_event"
    assert result_a[1]["event_type"] == "tick"
    assert result_b == result_a


async def test_events_slow_subscriber_does_not_block_fast_subscriber() -> None:
    loop_runner = FakeLoopRunner()
    service = RuntimeChatService(loop_runner)
    await service.start()
    try:
        fast_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        slow_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        service._subscribers.add(fast_queue)
        service._subscribers.add(slow_queue)

        for i in range(10):
            await loop_runner.queues.push_event("tick", str(i))

        await asyncio.sleep(0.3)

        assert fast_queue.qsize() == 10
        assert slow_queue.qsize() == 2
    finally:
        await service.stop()
