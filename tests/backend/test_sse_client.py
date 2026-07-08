"""Contract tests for the HTTP-based SSEProgenitorClient (backend <-> runtime).

Runs a real ``ProgenitorHTTPServer`` (port=0) backed by fakes and exercises the
backend client against it, mirroring ``tests/progenitor/test_http_chat.py`` on
the runtime side.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.backend.config import ChatServerConfig
from app.backend.sse_client import SSEProgenitorClient
from app.progenitor.config import RuntimeConfig
from app.progenitor.runtime.http import ProgenitorHTTPServer
from app.progenitor.runtime.queues import RuntimeQueues

pytestmark = pytest.mark.asyncio


class _Wait:
    """Script item: suspend the bridge until ``event`` is set."""

    def __init__(self, event: asyncio.Event) -> None:
        self.event = event


class ScriptedBridge:
    def __init__(self, graph: Any, *, config: Any, state: dict[str, Any], script: Any) -> None:
        self.graph = graph
        self.config = config
        self.state = state
        self._script = script

    async def stream(self, message: str, *, thread_id: str):
        items = self._script(message) if callable(self._script) else self._script
        for item in items:
            if isinstance(item, _Wait):
                await item.event.wait()
                continue
            yield item


def make_bridge_factory(script: Any):
    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        return ScriptedBridge(graph, config=config, state=state, script=script)

    return factory


class FakeStateManager:
    async def get_current_state(self) -> dict[str, Any]:
        return {"thread_id": "thread-test", "cycle_count": 0}

    async def save_current_state(self, checkpoint: dict[str, Any]) -> None:
        pass


@dataclass
class FakeLoopRunner:
    graph: Any = field(default_factory=lambda: SimpleNamespace(astream=lambda *a, **k: None))

    def __post_init__(self) -> None:
        self.graph_lock = asyncio.Lock()
        self.config = SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=False))
        self.state: dict[str, Any] = {"thread_id": "progenitor_main", "cycle_count": 0}
        self.heartbeat_context = None
        self.queues = RuntimeQueues(RuntimeConfig())


@dataclass
class _RuntimeServer:
    server: ProgenitorHTTPServer
    loop_runner: FakeLoopRunner
    base_url: str


async def _start_runtime_server(*, bridge_factory: Any = None) -> _RuntimeServer:
    loop_runner = FakeLoopRunner()
    server = ProgenitorHTTPServer(
        state_manager=FakeStateManager(),
        loop_runner=loop_runner,
        port=0,
    )
    # Test seam: inject a scripted bridge factory instead of the real DeepAgentIPCStream.
    assert server._chat_service is not None
    if bridge_factory is not None:
        server._chat_service._bridge_factory = bridge_factory
    await server.start()
    return _RuntimeServer(server=server, loop_runner=loop_runner, base_url=server.base_url)


async def _stop_runtime_server(runtime: _RuntimeServer) -> None:
    await runtime.server.stop()


def _client_config(base_url: str) -> ChatServerConfig:
    return ChatServerConfig(progenitor_runtime_url=base_url)


async def test_send_message_folds_reasoning_and_collects_chunks() -> None:
    script = [
        {
            "type": "stream_event",
            "event_type": "reasoning_delta",
            "content": "thinking",
            "metadata": {},
        },
        {
            "type": "stream_event",
            "event_type": "reasoning_summary",
            "content": "thinking summary",
            "metadata": {"reasoning_tokens": 42},
        },
        {"type": "stream_chunk", "content": "Hello ", "is_final": False},
        {"type": "stream_chunk", "content": "world", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        response = await client.send_message("hi", request_id="req-1")
    finally:
        await client.stop()
        await _stop_runtime_server(runtime)

    assert response.response == "Hello world"
    assert response.reasoning == "thinking summary"
    assert response.reasoning_tokens == 42


async def test_send_message_stream_events_yields_verbatim_and_swallows_queued() -> None:
    script = [{"type": "stream_chunk", "content": "ok", "is_final": True}]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        events = [
            event async for event in client.send_message_stream_events("hi", request_id="req-2")
        ]
    finally:
        await client.stop()
        await _stop_runtime_server(runtime)

    assert events == [
        {
            "type": "stream_chunk",
            "content": "ok",
            "is_final": True,
            "request_id": "req-2",
        }
    ]


async def test_send_message_stream_events_raises_on_error_frame() -> None:
    def raising_script(_message: str):
        raise RuntimeError("kaboom")

    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(raising_script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        with pytest.raises(RuntimeError, match="kaboom"):
            async for _ in client.send_message_stream_events("hi", request_id="req-3"):
                pass
    finally:
        await client.stop()
        await _stop_runtime_server(runtime)


async def test_cancel_request_true_and_false() -> None:
    gate = asyncio.Event()
    script = [_Wait(gate), {"type": "stream_chunk", "content": "x", "is_final": True}]

    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    task = None
    try:
        assert (await client.cancel_request("unknown-request")) is False

        task = asyncio.create_task(client.send_message("hi", request_id="req-cancel-me"))
        await asyncio.sleep(0.2)
        assert (await client.cancel_request("req-cancel-me")) is True

        gate.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        if task is not None and not task.done():
            task.cancel()
        await client.stop()
        await _stop_runtime_server(runtime)


async def test_cancel_request_false_when_unreachable() -> None:
    client = SSEProgenitorClient(ChatServerConfig(progenitor_runtime_url="http://127.0.0.1:1"))
    assert (await client.cancel_request("anything")) is False


async def test_event_forwarding_from_runtime_broadcast() -> None:
    runtime = await _start_runtime_server()
    received: list[tuple[str, dict[str, Any]]] = []

    def callback(event_type: str, data: dict[str, Any]) -> None:
        received.append((event_type, data))

    client = SSEProgenitorClient(_client_config(runtime.base_url), event_callback=callback)
    try:
        await client.start()
        await asyncio.sleep(0.2)
        await runtime.loop_runner.queues.push_event("tick", "hello", {"n": 1})

        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.1)
    finally:
        await client.stop()
        await _stop_runtime_server(runtime)

    assert received
    event_type, payload = received[0]
    assert event_type == "tick"
    assert payload["content"] == "hello"
    assert payload["metadata"] == {"n": 1}


async def test_start_liveness_and_status() -> None:
    runtime = await _start_runtime_server()
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        assert client.is_running is False
        await client.start()
        assert client.is_running is True
        assert client.last_heartbeat() is not None

        status = client.get_status()
        assert status["mode"] == "http"
        assert status["base_url"] == runtime.base_url
        assert status["running"] is True
    finally:
        await client.stop()
        await _stop_runtime_server(runtime)


async def test_start_raises_when_runtime_unreachable() -> None:
    client = SSEProgenitorClient(ChatServerConfig(progenitor_runtime_url="http://127.0.0.1:1"))
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(client.start(), timeout=15.0)
