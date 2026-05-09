"""Tests for converting native DeepAgent/LangGraph streams to IPC items."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.progenitor.ipc.deepagent_stream import DeepAgentIPCStream


class _FakeGraph:
    def __init__(self, items):
        self.items = items
        self.last_kwargs = None

    async def astream(self, *args, **kwargs):
        self.last_kwargs = kwargs
        for item in self.items:
            yield item


def _make_config(*, reasoning_enabled: bool = False):
    return SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=reasoning_enabled))


async def _collect_items(bridge: DeepAgentIPCStream, message: str = "hello") -> list[dict]:
    return [item async for item in bridge.stream(message, thread_id="thread-1")]


@pytest.mark.asyncio
async def test_graph_message_events_stream_incrementally_without_duplicate_final():
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
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert graph.last_kwargs["stream_mode"] == ["messages", "updates", "custom"]
    assert [item for item in items if item["type"] == "stream_chunk"] == [
        {"type": "stream_chunk", "content": "Hel", "is_final": False},
        {"type": "stream_chunk", "content": "lo", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    assert bridge.last_response == "Hello"


@pytest.mark.asyncio
async def test_pending_response_still_streams_when_no_message_events_exist():
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
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert [item for item in items if item["type"] == "stream_chunk"] == [
        {"type": "stream_chunk", "content": "Hello", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]


@pytest.mark.asyncio
async def test_stream_items_include_reasoning_and_custom_events():
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
            {"custom_node": {"messages": [AIMessage(content="Hi")]}},
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "search",
                    "metadata": {"tool_name": "search"},
                },
            ),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(), state={})

    items = await _collect_items(bridge)

    assert {
        "type": "stream_event",
        "event_type": "reasoning_delta",
        "content": "Checked constraints. ",
        "metadata": {
            "message_chunk_type": "AIMessageChunk",
            "stream_metadata": {"langgraph_node": "respond"},
            "text_length": 21,
        },
    } in items
    assert {
        "type": "stream_event",
        "event_type": "tool_start",
        "content": "search",
        "metadata": {"tool_name": "search"},
    } in items
    assert {"type": "stream_chunk", "content": "Hi", "is_final": False} in items


@pytest.mark.asyncio
async def test_native_reasoning_deltas_stream_without_synthetic_preview():
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
                            {
                                "type": "reasoning",
                                "summary": [
                                    {"type": "summary_text", "text": reasoning_fragments[0]}
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
                                    {"type": "summary_text", "text": reasoning_fragments[1]}
                                ],
                            },
                            {"type": "text", "text": "Hi"},
                        ]
                    ),
                    {"langgraph_node": "respond"},
                ),
            ),
            ("updates", {"respond": {"pending_response": "Hi"}}),
        ]
    )
    bridge = DeepAgentIPCStream(graph, config=_make_config(reasoning_enabled=True), state={})

    items = await _collect_items(bridge)

    reasoning_events = [item for item in items if item.get("event_type") == "reasoning_delta"]
    assert "".join(item["content"] for item in reasoning_events) == "".join(reasoning_fragments)
    assert all("synthetic" not in item["metadata"] for item in reasoning_events)
    assert all("preview" not in item["metadata"] for item in reasoning_events)
