"""Tests for explicit new conversation command handling in ProgenitorLoop."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest


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
async def test_slash_new_resets_context_without_graph_call(tmp_path: Path) -> None:
    from app.progenitor.runtime.loop import ProgenitorLoop

    state_manager = MagicMock()
    state_manager.save_current_state = AsyncMock()
    state_manager.state_dir = tmp_path

    graph = MagicMock()
    graph.astream = AsyncMock(side_effect=AssertionError("Graph should not run for /new"))  # type: ignore[attr-defined]

    loop = ProgenitorLoop(
        graph=graph,
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )

    loop._state = {
        "run_id": "run_old",
        "cycle_count": 1,
        "actions_this_cycle": 0,
        "shutdown_requested": False,
        "messages": [{"role": "user", "content": "hello"}],
        "current_input": "/new",
    }

    await loop._run_graph_cycle("ignored", source="user_input")

    assert loop._state["run_id"] != "run_old"
    assert loop._state.get("messages") == []
    assert isinstance(loop._state.get("pending_response"), str)
    assert "Started a new conversation" in loop._state["pending_response"]
    assert not graph.astream.called
    state_manager.save_current_state.assert_awaited()

