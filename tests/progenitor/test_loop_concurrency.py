"""Concurrency and error-path tests for the main loop and IPC serialization."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.progenitor.ipc.client import IPCClient
from app.progenitor.ipc.server import IPCServer
from app.progenitor.runtime.loop import ProgenitorLoop


class _BlockingLoopRunner:
    def __init__(self) -> None:
        self._output_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._cycle_complete_event = asyncio.Event()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.enqueue_calls: list[str] = []
        self.queued_events: list[dict[str, Any]] = []
        self.queued_event_emitted = asyncio.Event()

    async def enqueue_input(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        del metadata
        self.enqueue_calls.append(content)
        if content == "first":
            self.first_started.set()
            await self.release_first.wait()
            reply = "first done"
        else:
            reply = "second done"

        await self._stream_queue.put({"type": "stream_chunk", "content": reply, "is_final": True})
        self._cycle_complete_event.set()

    async def get_stream_item(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._stream_queue.get(), timeout=timeout)
            return await self._stream_queue.get()
        except TimeoutError:
            return None

    async def get_stream_chunk(self, timeout: float | None = None) -> dict[str, Any] | None:
        return await self.get_stream_item(timeout=timeout)

    async def wait_cycle_complete(self, timeout: float | None = None) -> bool:
        try:
            if timeout is not None:
                await asyncio.wait_for(self._cycle_complete_event.wait(), timeout=timeout)
            else:
                await self._cycle_complete_event.wait()
            self._cycle_complete_event.clear()
            return True
        except TimeoutError:
            return False

    async def get_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._event_queue.get(), timeout=timeout)
            return await self._event_queue.get()
        except TimeoutError:
            return None

    async def _push_event(
        self,
        event_type: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = {"event_type": event_type, "content": content, "metadata": metadata or {}}
        self.queued_events.append(event)
        self.queued_event_emitted.set()


class _ExplodingGraph:
    async def astream(self, *_args, **_kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover


def _make_loop_config() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            max_input_queue_size=10,
            max_output_queue_size=1,
            max_stream_queue_size=10,
            idle_backoff_seconds=1.0,
            checkpoint_interval_seconds=60.0,
        ),
        llm=SimpleNamespace(reasoning_enabled=False),
    )


@pytest.mark.asyncio
async def test_second_client_request_waits_behind_in_flight_request():
    loop_runner = _BlockingLoopRunner()
    server = IPCServer(
        loop_runner=loop_runner, host="127.0.0.1", port=0, request_timeout_seconds=2.0
    )
    await server.start()
    assert server._server is not None
    server.port = server._server.sockets[0].getsockname()[1]

    client1 = IPCClient(host="127.0.0.1", port=server.port, timeout=2.0, auto_reconnect=False)
    client2 = IPCClient(host="127.0.0.1", port=server.port, timeout=2.0, auto_reconnect=False)

    try:
        assert await client1.connect()
        assert await client2.connect()

        first_task = asyncio.create_task(client1.send_message("first", request_id="req-1"))
        await asyncio.wait_for(loop_runner.first_started.wait(), timeout=1.0)

        second_task = asyncio.create_task(client2.send_message("second", request_id="req-2"))
        await asyncio.wait_for(loop_runner.queued_event_emitted.wait(), timeout=1.0)
        queued_event = loop_runner.queued_events[-1]

        assert queued_event["event_type"] == "message_queued"
        assert queued_event["metadata"] == {"request_id": "req-2", "streaming": False}
        assert first_task.done() is False
        assert second_task.done() is False

        loop_runner.release_first.set()

        assert await first_task == "first done"
        assert await second_task == "second done"
        assert loop_runner.enqueue_calls == ["first", "second"]
    finally:
        await client1.disconnect()
        await client2.disconnect()
        await server.stop()


@pytest.mark.asyncio
async def test_input_handler_surfaces_graph_failures_without_hanging():
    loop = ProgenitorLoop(
        graph=_ExplodingGraph(),
        state_manager=AsyncMock(),
        config=_make_loop_config(),
    )
    loop._state = {
        "thread_id": "thread-1",
        "cycle_count": 0,
        "messages": [],
    }
    loop.running = True
    loop._push_event = AsyncMock()  # type: ignore[method-assign]
    loop._checkpoint_state = AsyncMock()  # type: ignore[method-assign]
    loop._update_health_file = AsyncMock()  # type: ignore[method-assign]

    inputs = iter(["hello", None])

    async def _fake_get_input(timeout: float) -> str | None:
        del timeout
        value = next(inputs)
        if value is None:
            loop.running = False
        return value

    loop._get_input = _fake_get_input  # type: ignore[method-assign]

    await loop._input_handler_loop("boot-thread")

    chunk = await loop.get_stream_chunk(timeout=0.01)
    assert chunk == {"type": "stream_chunk", "content": "Error: boom", "is_final": True}
    assert await loop.get_stream_chunk(timeout=0.01) is None
    assert loop._last_cycle_response == "Error: boom"
    loop._push_event.assert_any_await("error", "boom")
    assert loop._cycle_complete_event.is_set() is True


@pytest.mark.asyncio
async def test_send_output_raises_when_output_queue_stays_full():
    loop = ProgenitorLoop(
        graph=AsyncMock(),
        state_manager=AsyncMock(),
        config=_make_loop_config(),
    )

    class _BlockingQueue:
        maxsize = 1

        def qsize(self) -> int:
            return 1

        async def put(self, item: str) -> None:
            del item
            await asyncio.Future()

    loop._output_queue = _BlockingQueue()

    with (
        pytest.raises(RuntimeError, match="Output queue remained full"),
        pytest.MonkeyPatch.context() as patcher,
    ):
        patcher.setattr("app.progenitor.runtime.loop.OUTPUT_QUEUE_PUT_TIMEOUT_SECONDS", 0.01)
        await loop._send_output("blocked")
