"""Tests for ProgenitorLoop A2A completion recording."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.runtime.loop import ProgenitorLoop
from app.progenitor.state.models import A2ATaskRecord


@dataclass
class _RuntimeConfig:
    idle_backoff_seconds: int = 30
    max_backoff_seconds: int = 300
    backoff_multiplier: float = 1.5
    max_input_queue_size: int = 5
    max_output_queue_size: int = 5
    max_stream_queue_size: int = 10
    timezone: str = "UTC"


@dataclass
class _Config:
    runtime: _RuntimeConfig = field(default_factory=_RuntimeConfig)


@pytest.mark.asyncio
async def test_record_completed_a2a_task_persists_tool_message(tmp_path: Path) -> None:
    state_manager = MagicMock()
    state_manager.log_conversation = AsyncMock()
    state_manager.save_messages = AsyncMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),
        state_manager=state_manager,
        config=cast(Any, _Config()),
    )
    loop._state = {"messages": []}

    record = A2ATaskRecord(
        task_id="observe-5",
        agent_name="observe_agent",
        status="completed",
        summary="Observe task completed.",
    )

    await loop._record_completed_a2a_task(record)
    await loop._record_completed_a2a_task(record)

    assert len(loop._state["messages"]) == 1
    message = loop._state["messages"][0]
    assert message.role == "tool"
    assert message.metadata["type"] == "a2a_task_completion"
    assert message.metadata["task_id"] == "observe-5"
    assert "observe agent task completed" in message.content
    state_manager.log_conversation.assert_awaited_once()
    state_manager.save_messages.assert_awaited_once()
