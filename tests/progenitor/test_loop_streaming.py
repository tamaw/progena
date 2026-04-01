"""Tests for token streaming in ProgenitorLoop."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.progenitor.runtime.loop import ProgenitorLoop


class _FakeGraph:
    def __init__(self, items):
        self.items = items
        self.last_kwargs = None

    async def astream(self, *args, **kwargs):
        self.last_kwargs = kwargs
        for item in self.items:
            yield item


def _make_config():
    return SimpleNamespace(
        runtime=SimpleNamespace(
            max_input_queue_size=10,
            max_output_queue_size=10,
            max_stream_queue_size=10,
            idle_backoff_seconds=1.0,
        ),
        llm=SimpleNamespace(reasoning_enabled=False),
    )


def _make_loop_state():
    return {
        "cycle_count": 1,
        "messages": [],
        "current_input": "hello",
    }


async def _collect_stream_chunks(loop: ProgenitorLoop) -> list[dict]:
    chunks: list[dict] = []
    while True:
        chunk = await loop.get_stream_chunk(timeout=0.01)
        if chunk is None:
            break
        chunks.append(chunk)
        if chunk.get("is_final"):
            break
    return chunks


class TestProgenitorLoopStreaming:
    @pytest.mark.asyncio
    async def test_graph_message_events_stream_incrementally_without_duplicate_final(self):
        graph = _FakeGraph([
            ("messages", (AIMessageChunk(content="Hel"), {"langgraph_node": "respond"})),
            ("messages", (AIMessageChunk(content="lo"), {"langgraph_node": "respond"})),
            ("updates", {
                "respond": {
                    "pending_response": "Hello",
                    "messages": [AIMessage(content="Hello")],
                }
            }),
        ])
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop._streaming_enabled = True

        await loop._run_graph_cycle("run-1", source="user_input")
        chunks = await _collect_stream_chunks(loop)

        assert graph.last_kwargs["stream_mode"] == ["messages", "updates"]
        assert chunks == [
            {"type": "stream_chunk", "content": "Hel", "is_final": False},
            {"type": "stream_chunk", "content": "lo", "is_final": False},
            {"type": "stream_chunk", "content": "", "is_final": True},
        ]
        assert loop._last_cycle_response == "Hello"

    @pytest.mark.asyncio
    async def test_pending_response_still_streams_when_no_message_events_exist(self):
        graph = _FakeGraph([
            ("updates", {
                "respond": {
                    "pending_response": "Hello",
                    "messages": [AIMessage(content="Hello")],
                }
            }),
        ])
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop._streaming_enabled = True

        await loop._run_graph_cycle("run-2", source="user_input")
        chunks = await _collect_stream_chunks(loop)

        assert chunks == [
            {"type": "stream_chunk", "content": "Hello", "is_final": True},
        ]
