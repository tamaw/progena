"""Tests for the in-process Progenitor service."""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.progenitor.service import ProgenitorService


class StubStateManager:
    """Minimal state manager stub for service tests."""

    def __init__(self, run_id: str = "run-123"):
        self.run_id = run_id

    async def get_current_state(self) -> dict[str, Any]:
        return {"run_id": self.run_id}


class StubGraph:
    """Graph stub that records the last invocation."""

    def __init__(self):
        self.last_input: dict[str, Any] | None = None
        self.last_config: dict[str, Any] | None = None

    async def ainvoke(self, graph_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        self.last_input = graph_input
        self.last_config = config
        return {"messages": [AIMessage(content="assistant reply")]}


@pytest.mark.asyncio
async def test_handle_message_uses_persisted_thread_id_and_extracts_ai_message() -> None:
    """Service mode should preserve conversation thread continuity."""
    service = object.__new__(ProgenitorService)
    service._initialized = True
    service._message_count = 0
    service._session_id = "session-1"
    service.state_manager = StubStateManager(run_id="persisted-run")
    service.graph = StubGraph()
    service.llm_client = None
    service.tool_registry = None
    service.project_root = None
    service.config = None
    service._lock = None

    response = await service.handle_message("Hello", request_id="req-1")

    assert response == "assistant reply"
    assert service.graph.last_config == {"configurable": {"thread_id": "persisted-run"}}
    assert isinstance(service.graph.last_input, dict)
    messages = service.graph.last_input["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Hello"
