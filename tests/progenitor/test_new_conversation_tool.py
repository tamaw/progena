"""Tests for new_conversation system tool integration with DeepAgent + ProgenitorLoop."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_new_conversation_tool_is_exposed_and_mutates_loop_state() -> None:
    from app.progenitor.agent.adapters import create_deepagent_tools

    class _FakeLoop:  # noqa: D401
        """Minimal loop stub for get_current_loop()."""

        def __init__(self):
            self._state = {
                "thread_id": "thread_old",
                "messages": [{"role": "user", "content": "hello"}],
            }

    fake_loop = _FakeLoop()

    with patch("app.progenitor.agent.adapters.get_current_loop", return_value=fake_loop):
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
    mcp_refresh_seconds: int = 900
    registry_refresh_seconds: int = 900
    max_input_queue_size: int = 5
    max_output_queue_size: int = 5
    max_stream_queue_size: int = 10


@dataclass
class _MockConfig:
    runtime: _MockRuntimeConfig = field(default_factory=_MockRuntimeConfig)


@pytest.mark.asyncio
async def test_loop_clears_messages_when_thread_id_changes_mid_cycle(tmp_path: Path) -> None:
    from app.progenitor.runtime.heartbeat import ProgenitorLoop

    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),  # replaced below
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )

    class _FakeGraph:
        async def ainvoke(self, graph_input, config):  # noqa: ANN001
            # Simulate new_conversation being invoked during the cycle.
            loop._state["thread_id"] = "thread_new"
            loop._state["messages"] = [{"role": "user", "content": "should_be_cleared"}]
            return {
                "messages": [{"role": "user", "content": "before"}],
                "pending_response": "ok",
            }

    loop.graph = _FakeGraph()
    loop.heartbeat_context.agent_graph = loop.graph
    loop._state = {
        "thread_id": "thread_old",
        "cycle_count": 1,
        "actions_this_cycle": 0,
        "shutdown_requested": False,
        "messages": [{"role": "user", "content": "hello"}],
        "tool_calls": [],
        "attachments": [],
        "queued_input": "reset the conversation",
    }
    loop.heartbeat_context.live_state = loop.state

    updates = await loop._heartbeat_graph.ainvoke(
        cast(Any, loop.state),
        context=loop.heartbeat_context,
    )
    loop.apply_graph_updates(cast(dict[str, Any], updates))

    assert loop.state["thread_id"] == "thread_new"
    assert loop.state["messages"] == []


@pytest.mark.asyncio
async def test_loop_preserves_persisted_thread_id_on_startup(tmp_path: Path) -> None:
    from app.progenitor.runtime.heartbeat import ProgenitorLoop

    state_manager = MagicMock()
    state_manager.get_current_state = AsyncMock(return_value={"thread_id": "thread_persisted"})
    state_manager.get_a2a_tasks = AsyncMock(return_value=[])
    state_manager.save_current_state = AsyncMock()
    state_manager.save_health = AsyncMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )

    loop._input_handler_loop = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await loop.run_forever()

    assert loop.state["thread_id"] == "thread_persisted"
