"""Security and boundary tests for the IPC protocol/server."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

import pytest

from app.progenitor.ipc.protocol import (
    MAX_MESSAGE_SIZE,
    IPCMessage,
    MessageType,
    read_message,
)
from app.progenitor.ipc.server import IPCServer


class _LoopStub:
    def __init__(self) -> None:
        self._output_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._cycle_complete_event = asyncio.Event()
        self._cycle_complete_event.set()

    async def enqueue_input(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        del metadata
        await self._stream_queue.put(
            {"type": "stream_chunk", "content": f"echo:{content}", "is_final": True}
        )
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
        self._event_queue.put_nowait(
            {"event_type": event_type, "content": content, "metadata": metadata or {}}
        )


def _build_reader(payload: bytes, *, length: int | None = None) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">I", len(payload) if length is None else length))
    if payload:
        reader.feed_data(payload)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_read_message_rejects_zero_length_frames():
    reader = _build_reader(b"", length=0)

    with pytest.raises(ValueError, match="length 0"):
        await read_message(reader, timeout=1.0)


@pytest.mark.asyncio
async def test_read_message_rejects_frames_larger_than_max():
    reader = _build_reader(b"{}", length=MAX_MESSAGE_SIZE + 1)

    with pytest.raises(ValueError, match="exceeds maximum"):
        await read_message(reader, timeout=1.0)


@pytest.mark.asyncio
async def test_read_message_rejects_malformed_json_payloads():
    payload = b'{"type":"user_message","content":'
    reader = _build_reader(payload)

    with pytest.raises(json.JSONDecodeError):
        await read_message(reader, timeout=1.0)


@pytest.mark.asyncio
async def test_ipc_server_survives_rapid_connect_disconnect():
    server = IPCServer(loop_runner=_LoopStub(), host="127.0.0.1", port=0)
    await server.start()
    assert server._server is not None
    server.port = server._server.sockets[0].getsockname()[1]

    try:
        for _ in range(10):
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            ready = await read_message(reader, timeout=1.0)
            assert isinstance(ready, IPCMessage)
            assert ready.type == MessageType.READY
            writer.close()
            await writer.wait_closed()

        await asyncio.sleep(0.2)
        assert server.client_count == 0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_ipc_server_rejects_clients_beyond_max_capacity():
    server = IPCServer(loop_runner=_LoopStub(), host="127.0.0.1", port=0, max_clients=2)
    await server.start()
    assert server._server is not None
    server.port = server._server.sockets[0].getsockname()[1]

    connections: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
    try:
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            ready = await read_message(reader, timeout=1.0)
            assert ready is not None
            assert ready.type == MessageType.READY
            connections.append((reader, writer))

        reader3, writer3 = await asyncio.open_connection("127.0.0.1", server.port)
        rejected = await read_message(reader3, timeout=1.0)

        assert rejected is not None
        assert rejected.type == MessageType.ERROR
        assert rejected.error_code == "MAX_CLIENTS"
        assert rejected.content == "Server at capacity"

        disconnected = await read_message(reader3, timeout=1.0)
        assert disconnected is None
        assert server.client_count == 2

        writer3.close()
        await writer3.wait_closed()
    finally:
        for _, writer in connections:
            writer.close()
            await writer.wait_closed()
        await server.stop()
