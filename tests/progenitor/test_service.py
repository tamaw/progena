"""Tests for the in-process Progenitor service."""

from collections.abc import AsyncIterator
from typing import Any

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
async def test_handle_message_stream_events_emit_message_and_node_updates() -> None:
    """Service streaming should expose structured events plus text chunks."""
    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(thread_id="persisted-thread")
    service.graph = StubStreamingGraph(
        [
            ("messages", (AIMessageChunk(content="Hi"), {"langgraph_node": "respond"})),
            (
                "updates",
                {
                    "respond": {
                        "messages": [AIMessage(content="Hi")],
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
        "stream_mode": ["messages", "updates"],
    }
    assert events == [
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
            "type": "stream_chunk",
            "content": "",
            "is_final": True,
            "request_id": "req-stream",
        },
    ]
