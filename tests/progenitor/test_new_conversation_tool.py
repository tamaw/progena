"""Tests for new_conversation system tool integration with DeepAgent + ProgenitorLoop."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_new_conversation_tool_is_exposed_and_mutates_loop_state() -> None:
    from app.progenitor.agent.tools_adapter import create_deepagent_tools

    class _FakeLoop:  # noqa: D401
        """Minimal loop stub for get_current_loop()."""

        def __init__(self):
            self._state = {
                "thread_id": "thread_old",
                "messages": [{"role": "user", "content": "hello"}],
            }

    fake_loop = _FakeLoop()

    with patch("app.progenitor.agent.tools_adapter.get_current_loop", return_value=fake_loop):
        tools = create_deepagent_tools(
            tool_registry=MagicMock(), llm_client=MagicMock(), state_manager=None
        )

        new_conv = next(t for t in tools if t.name == "new_conversation")
        result = await new_conv.ainvoke({"request": "reset the conversation"})

    assert isinstance(result, dict)
    assert result.get("reset") is True
    assert fake_loop._state["thread_id"] != "thread_old"
    assert fake_loop._state["messages"] == []


@dataclass
class _MockRuntimeConfig:
    idle_backoff_seconds: int = 30
    max_input_queue_size: int = 5
    max_output_queue_size: int = 5
    max_stream_queue_size: int = 10


@dataclass
class _MockConfig:
    runtime: _MockRuntimeConfig = field(default_factory=_MockRuntimeConfig)


@pytest.mark.asyncio
async def test_loop_clears_messages_when_thread_id_changes_mid_cycle(tmp_path: Path) -> None:
    from app.progenitor.runtime.loop import ProgenitorLoop

    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),  # replaced below
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )
    loop._streaming_enabled = False
    loop._response_was_streamed = False
    loop._stream_final_sent = False

    class _FakeGraph:
        async def astream(self, graph_input, config):  # noqa: ANN001
            yield {
                "node": {
                    "messages": [{"role": "user", "content": "before"}],
                    "pending_response": "ok",
                }
            }
            # Simulate new_conversation being invoked during the cycle.
            loop._state["thread_id"] = "thread_new"
            loop._state["messages"] = [{"role": "user", "content": "should_be_cleared"}]
            yield {}

    loop.graph = _FakeGraph()
    loop._state = {
        "thread_id": "thread_old",
        "cycle_count": 1,
        "actions_this_cycle": 0,
        "shutdown_requested": False,
        "messages": [{"role": "user", "content": "hello"}],
        "tool_calls": [],
        "attachments": [],
        "current_input": "reset the conversation",
    }

    await loop._run_graph_cycle("ignored", source="user_input")

    assert loop._state["thread_id"] == "thread_new"
    assert loop._state["messages"] == []


@pytest.mark.asyncio
async def test_loop_preserves_persisted_thread_id_on_startup(tmp_path: Path) -> None:
    from app.progenitor.runtime.loop import ProgenitorLoop

    state_manager = MagicMock()
    state_manager.get_current_state = AsyncMock(return_value={"thread_id": "thread_persisted"})
    state_manager.get_a2a_tasks = AsyncMock(return_value=[])
    state_manager.save_current_state = AsyncMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )

    loop._input_handler_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]
    loop._checkpoint_state = AsyncMock()  # type: ignore[method-assign]
    loop._update_health_file = AsyncMock()  # type: ignore[method-assign]
    loop._push_event = AsyncMock()  # type: ignore[method-assign]

    with patch("app.progenitor.runtime.loop.A2ATaskTracker") as tracker_cls:
        tracker = MagicMock()
        tracker.run = AsyncMock(return_value=None)
        tracker_cls.return_value = tracker
        await loop.run_forever()

    assert loop._state["thread_id"] == "thread_persisted"
