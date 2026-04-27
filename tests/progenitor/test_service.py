"""Tests for the in-process Progenitor service."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.progenitor.service import ProgenitorService


class StubStateManager:
    """Minimal state manager stub for service tests."""

    def __init__(self, thread_id: str = "thread-123"):
        self.thread_id = thread_id

    async def get_current_state(self) -> dict[str, Any]:
        return {"thread_id": self.thread_id}


class StubGraph:
    """Graph stub that records the last invocation."""

    def __init__(self):
        self.last_input: dict[str, Any] | None = None
        self.last_config: dict[str, Any] | None = None

    async def ainvoke(self, graph_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.last_input = graph_input
        self.last_config = config
        return {"messages": [AIMessage(content="assistant reply")]}


class StubStreamingGraph:
    """Graph stub that yields LangGraph-style stream items."""

    def __init__(self, items: list[Any]):
        self.items = items
        self.last_kwargs: dict[str, Any] | None = None

    async def astream(self, graph_input: dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        self.last_kwargs = kwargs
        for item in self.items:
            yield item


@pytest.mark.asyncio
async def test_handle_message_uses_persisted_thread_id_and_extracts_ai_message() -> None:
    """Service mode should preserve conversation thread continuity."""
    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(thread_id="persisted-thread")
    service.graph = StubGraph()
    service.llm_client = None
    service.tool_registry = None
    service.project_root = None
    service.config = None
    service._lock = None

    response = await service.handle_message("Hello", request_id="req-1")

    assert response == "assistant reply"
    assert service.graph.last_config == {"configurable": {"thread_id": "persisted-thread"}}
    assert isinstance(service.graph.last_input, dict)
    messages = service.graph.last_input["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Hello"


@pytest.mark.asyncio
async def test_handle_message_include_reasoning_returns_reasoning_details() -> None:
    """Service mode should expose reasoning text and token counts for non-stream calls."""
    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(thread_id="persisted-thread")
    service.graph = StubGraph()
    service.graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                AIMessage(
                    content=[
                        {
                            "type": "reasoning",
                            "summary": [
                                {
                                    "type": "summary_text",
                                    "text": "Checked constraints before answering.",
                                }
                            ],
                        },
                        {"type": "text", "text": "assistant reply"},
                    ],
                    response_metadata={
                        "token_usage": {"completion_tokens_details": {"reasoning_tokens": 9}}
                    },
                )
            ]
        }
    )
    service.llm_client = None
    service.tool_registry = None
    service.project_root = None
    service.config = None
    service._lock = None

    response = await service.handle_message(
        "Hello",
        request_id="req-1",
        include_reasoning=True,
    )

    assert response == {
        "response": "assistant reply",
        "reasoning": "Checked constraints before answering.",
        "reasoning_tokens": 9,
    }


@pytest.mark.asyncio
async def test_handle_message_stream_events_emit_reasoning_message_and_node_updates() -> None:
    """Service streaming should expose reasoning, text, and node events."""
    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(thread_id="persisted-thread")
    service.graph = StubStreamingGraph(
        [
            (
                "messages",
                (
                    AIMessageChunk(
                        content=[
                            {
                                "type": "reasoning",
                                "summary": [{"type": "summary_text", "text": "Checked inputs. "}],
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
                        "messages": [
                            {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "reasoning",
                                        "summary": [
                                            {
                                                "type": "summary_text",
                                                "text": "Checked inputs before answering.",
                                            }
                                        ],
                                    },
                                    {"type": "text", "text": "Hi"},
                                ],
                                "response_metadata": {
                                    "token_usage": {
                                        "completion_tokens_details": {"reasoning_tokens": 9}
                                    }
                                },
                            }
                        ],
                    }
                },
            ),
        ]
    )
    service.llm_client = None
    service.tool_registry = None
    service.project_root = None
    service.config = None
    service._lock = None

    events = [
        event
        async for event in service.handle_message_stream_events("Hello", request_id="req-stream")
    ]

    assert service.graph.last_kwargs == {
        "config": {"configurable": {"thread_id": "persisted-thread"}},
        "stream_mode": ["messages", "updates", "custom"],
    }
    assert events == [
        {
            "type": "stream_event",
            "event_type": "reasoning_delta",
            "content": "Checked inputs. ",
            "request_id": "req-stream",
            "metadata": {
                "message_chunk_type": "AIMessageChunk",
                "stream_metadata": {"langgraph_node": "respond"},
                "text_length": 16,
            },
        },
        {
            "type": "stream_event",
            "event_type": "message_delta",
            "content": "Hi",
            "request_id": "req-stream",
            "metadata": {
                "message_chunk_type": "AIMessageChunk",
                "stream_metadata": {"langgraph_node": "respond"},
                "text_length": 2,
            },
        },
        {
            "type": "stream_chunk",
            "content": "Hi",
            "is_final": False,
            "request_id": "req-stream",
        },
        {
            "type": "stream_event",
            "event_type": "node_update",
            "content": "",
            "request_id": "req-stream",
            "metadata": {
                "node_name": "respond",
                "output_keys": ["messages"],
                "contains_messages": True,
                "contains_pending_response": False,
            },
        },
        {
            "type": "stream_event",
            "event_type": "reasoning_summary",
            "content": "Checked inputs before answering.",
            "request_id": "req-stream",
            "metadata": {
                "reasoning_tokens": 9,
                "text_length": 32,
            },
        },
        {
            "type": "stream_chunk",
            "content": "",
            "is_final": True,
            "request_id": "req-stream",
        },
    ]


@pytest.mark.asyncio
async def test_handle_message_stream_events_forwards_custom_tool_events() -> None:
    """Custom-mode payloads from middleware/tools should pass through to the IPC."""
    from app.progenitor.service import ProgenitorService

    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(thread_id="persisted-thread")
    service.graph = StubStreamingGraph(
        [
            (
                "custom",
                {
                    "event_type": "tool_start",
                    "content": "search",
                    "metadata": {"tool_name": "search", "tool_call_id": "call_1"},
                },
            ),
            (
                "custom",
                {
                    "event_type": "tool_result",
                    "content": "search",
                    "metadata": {
                        "tool_name": "search",
                        "tool_call_id": "call_1",
                        "duration_ms": 12.0,
                        "success": True,
                        "result_preview": "ok",
                    },
                },
            ),
            (
                "custom",
                {"event_type": "rogue_type", "metadata": {"x": 1}},
            ),
        ]
    )
    service.llm_client = None
    service.tool_registry = None
    service.project_root = None
    service.config = None
    service._lock = None

    events = [
        event async for event in service.handle_message_stream_events("hi", request_id="req-tools")
    ]
    stream_events = [e for e in events if e.get("type") == "stream_event"]

    assert [e["event_type"] for e in stream_events] == [
        "tool_start",
        "tool_result",
        "custom_event",
    ]
    assert stream_events[0]["metadata"]["tool_name"] == "search"
    assert stream_events[1]["metadata"]["success"] is True
    assert stream_events[2]["metadata"]["original_event_type"] == "rogue_type"
    for ev in stream_events:
        assert ev["request_id"] == "req-tools"
