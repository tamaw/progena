"""Tests for loop shutdown behavior."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.progenitor.runtime.loop import ProgenitorLoop


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
async def test_cancel_task_and_log_surfaces_teardown_errors(tmp_path: Path, caplog):
    state_manager = MagicMock()
    state_manager.state_dir = tmp_path

    loop = ProgenitorLoop(
        graph=MagicMock(),
        state_manager=state_manager,
        config=cast(Any, _MockConfig()),
    )
    waiter = asyncio.Event()

    async def failing_task() -> None:
        try:
            await waiter.wait()
        except asyncio.CancelledError:
            raise RuntimeError("input teardown failed")

    task = asyncio.create_task(failing_task())
    await asyncio.sleep(0)

    with caplog.at_level("WARNING"):
        await loop._cancel_task_and_log(task, label="input task")

    warning_messages = [record.getMessage() for record in caplog.records]
    assert any(
        "input task shutdown" in message and "input teardown failed" in message
        for message in warning_messages
    )
