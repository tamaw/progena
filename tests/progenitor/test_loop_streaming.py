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


def _make_config(*, reasoning_enabled: bool = False):
    return SimpleNamespace(
        runtime=SimpleNamespace(
            max_input_queue_size=10,
            max_output_queue_size=10,
            max_stream_queue_size=10,
            idle_backoff_seconds=1.0,
            checkpoint_interval_seconds=60.0,
        ),
        llm=SimpleNamespace(reasoning_enabled=reasoning_enabled),
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
        chunk = await loop.stream.get_stream_chunk(timeout=0.01)
        if chunk is None:
            break
        chunks.append(chunk)
        if chunk.get("is_final"):
            break
    return chunks


async def _collect_stream_items(loop: ProgenitorLoop) -> list[dict]:
    items: list[dict] = []
    while True:
        item = await loop.stream.get_stream_item(timeout=0.01)
        if item is None:
            break
        items.append(item)
        if item.get("type") == "stream_chunk" and item.get("is_final"):
            break
    return items


class TestProgenitorLoopStreaming:
    @pytest.mark.asyncio
    async def test_graph_message_events_stream_incrementally_without_duplicate_final(self):
        graph = _FakeGraph(
            [
                ("messages", (AIMessageChunk(content="Hel"), {"langgraph_node": "respond"})),
                ("messages", (AIMessageChunk(content="lo"), {"langgraph_node": "respond"})),
                (
                    "updates",
                    {
                        "respond": {
                            "pending_response": "Hello",
                            "messages": [AIMessage(content="Hello")],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop.stream.enabled = True

        await loop._run_graph_cycle("run-1", source="user_input")
        chunks = await _collect_stream_chunks(loop)

        assert graph.last_kwargs["stream_mode"] == ["messages", "updates", "custom"]
        assert chunks == [
            {"type": "stream_chunk", "content": "Hel", "is_final": False},
            {"type": "stream_chunk", "content": "lo", "is_final": False},
            {"type": "stream_chunk", "content": "", "is_final": True},
        ]
        assert loop._last_cycle_response == "Hello"

    @pytest.mark.asyncio
    async def test_pending_response_still_streams_when_no_message_events_exist(self):
        graph = _FakeGraph(
            [
                (
                    "updates",
                    {
                        "respond": {
                            "pending_response": "Hello",
                            "messages": [AIMessage(content="Hello")],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop.stream.enabled = True

        await loop._run_graph_cycle("run-2", source="user_input")
        chunks = await _collect_stream_chunks(loop)

        assert chunks == [
            {"type": "stream_chunk", "content": "Hello", "is_final": False},
            {"type": "stream_chunk", "content": "", "is_final": True},
        ]

    @pytest.mark.asyncio
    async def test_stream_items_include_structured_events_without_breaking_chunk_reader(self):
        graph = _FakeGraph(
            [
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[
                                {
                                    "type": "reasoning",
                                    "summary": [
                                        {"type": "summary_text", "text": "Checked constraints. "}
                                    ],
                                },
                                {"type": "text", "text": "Hi"},
                            ]
                        ),
                        {"langgraph_node": "respond"},
                    ),
                ),
                (
                    "updates",
                    {
                        "respond": {
                            "pending_response": "Hi",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "reasoning",
                                            "summary": [
                                                {
                                                    "type": "summary_text",
                                                    "text": "Checked constraints before answering.",
                                                }
                                            ],
                                        },
                                        {"type": "text", "text": "Hi"},
                                    ],
                                    "response_metadata": {
                                        "token_usage": {
                                            "completion_tokens_details": {"reasoning_tokens": 17}
                                        }
                                    },
                                }
                            ],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop.stream.enabled = True

        await loop._run_graph_cycle("run-structured", source="user_input")
        items = await _collect_stream_items(loop)

        assert items == [
            {
                "type": "stream_event",
                "event_type": "reasoning_delta",
                "content": "Checked constraints. ",
                "metadata": {
                    "message_chunk_type": "AIMessageChunk",
                    "stream_metadata": {"langgraph_node": "respond"},
                    "text_length": 21,
                },
            },
            {"type": "stream_chunk", "content": "Hi", "is_final": False},
            {
                "type": "stream_event",
                "event_type": "message_delta",
                "content": "Hi",
                "metadata": {
                    "message_chunk_type": "AIMessageChunk",
                    "stream_metadata": {"langgraph_node": "respond"},
                    "text_length": 2,
                },
            },
            {
                "type": "stream_event",
                "event_type": "node_update",
                "content": "",
                "metadata": {
                    "node_name": "respond",
                    "output_keys": ["pending_response", "messages"],
                    "contains_messages": True,
                    "contains_pending_response": True,
                },
            },
            {
                "type": "stream_event",
                "event_type": "reasoning_summary",
                "content": "Checked constraints before answering.",
                "metadata": {
                    "reasoning_tokens": 17,
                    "text_length": 37,
                },
            },
            {"type": "stream_chunk", "content": "", "is_final": True},
        ]

    @pytest.mark.asyncio
    async def test_native_reasoning_deltas_stream_without_synthetic_preview(self):
        reasoning_fragments = [
            "**Calculating pencil costs**\n\nI'm",
            " figuring it out.",
        ]
        graph = _FakeGraph(
            [
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[
                                {"type": "reasoning", "summary": []},
                            ]
                        ),
                        {"langgraph_node": "respond"},
                    ),
                ),
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[
                                {
                                    "type": "reasoning",
                                    "summary": [
                                        {
                                            "type": "summary_text",
                                            "text": reasoning_fragments[0],
                                        }
                                    ],
                                }
                            ]
                        ),
                        {"langgraph_node": "respond"},
                    ),
                ),
                (
                    "messages",
                    (
                        AIMessageChunk(
                            content=[
                                {
                                    "type": "reasoning",
                                    "summary": [
                                        {
                                            "type": "summary_text",
                                            "text": reasoning_fragments[1],
                                        }
                                    ],
                                },
                                {"type": "text", "text": "Hi"},
                            ]
                        ),
                        {"langgraph_node": "respond"},
                    ),
                ),
                (
                    "updates",
                    {
                        "respond": {
                            "pending_response": "Hi",
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "reasoning",
                                            "summary": [
                                                {
                                                    "type": "summary_text",
                                                    "text": "Checked the steps before answering.",
                                                }
                                            ],
                                        },
                                        {"type": "text", "text": "Hi"},
                                    ],
                                }
                            ],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(reasoning_enabled=True),
        )
        loop._state = _make_loop_state()
        loop.stream.enabled = True

        await loop._run_graph_cycle("run-native-reasoning", source="user_input")
        items = await _collect_stream_items(loop)

        reasoning_events = [item for item in items if item.get("event_type") == "reasoning_delta"]
        assert len(reasoning_events) >= 2
        assert "".join(item["content"] for item in reasoning_events) == "".join(reasoning_fragments)
        assert all("synthetic" not in item["metadata"] for item in reasoning_events)
        assert all("preview" not in item["metadata"] for item in reasoning_events)

        first_text_message_delta_index = next(
            index
            for index, item in enumerate(items)
            if item.get("event_type") == "message_delta" and item.get("content")
        )
        reasoning_indices = [
            index for index, item in enumerate(items) if item.get("event_type") == "reasoning_delta"
        ]
        assert max(reasoning_indices) < first_text_message_delta_index
        assert items[first_text_message_delta_index]["content"] == "Hi"

    @pytest.mark.asyncio
    async def test_streamed_response_is_captured_without_queue_output_when_streaming_disabled(self):
        graph = _FakeGraph(
            [
                ("messages", (AIMessageChunk(content="Hel"), {"langgraph_node": "model"})),
                ("messages", (AIMessageChunk(content="lo"), {"langgraph_node": "model"})),
                (
                    "updates",
                    {
                        "model": {
                            "messages": [AIMessage(content="Hello")],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = _make_loop_state()
        loop.stream.enabled = False

        await loop._run_graph_cycle("run-3", source="user_input")
        chunks = await _collect_stream_chunks(loop)

        assert chunks == []
        assert loop.stream.streamed_response_content == "Hello"
        assert loop._last_cycle_response == "Hello"

    @pytest.mark.asyncio
    async def test_scheduled_input_emits_scheduled_response_from_streamed_content(self):
        graph = _FakeGraph(
            [
                ("messages", (AIMessageChunk(content="Hel"), {"langgraph_node": "model"})),
                ("messages", (AIMessageChunk(content="lo"), {"langgraph_node": "model"})),
                (
                    "updates",
                    {
                        "model": {
                            "messages": [AIMessage(content="Hello")],
                        }
                    },
                ),
            ]
        )
        loop = ProgenitorLoop(
            graph=graph,
            state_manager=AsyncMock(),
            config=_make_config(),
        )
        loop._state = {
            **_make_loop_state(),
            "thread_id": "thread-scheduled",
            "current_backoff_seconds": 1.0,
            "idle_cycles": 0,
            "actions_this_cycle": 0,
        }
        loop.running = True
        loop._push_event = AsyncMock()  # type: ignore[method-assign]

        inputs = iter(
            [
                "[Scheduled Task]\nSay hi",
                None,
            ]
        )

        async def _fake_get_input(timeout: float) -> str | None:
            del timeout
            try:
                value = next(inputs)
            except StopIteration:
                loop.running = False
                return None
            if value is None:
                loop.running = False
            return value

        loop._get_input = _fake_get_input  # type: ignore[method-assign]

        await loop._input_handler_loop("thread-scheduled")
        chunks = await _collect_stream_chunks(loop)

        assert chunks == []
        loop._push_event.assert_any_await(
            "scheduled_response",
            "Hello",
            {
                "schedule_message": "Say hi",
                "status": "completed",
            },
        )
