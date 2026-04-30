"""Tests for bounded queue functionality in ProgenitorLoop."""

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@dataclass
class MockRuntimeConfig:
    """Mock runtime config for testing."""

    idle_backoff_seconds: int = 30
    max_backoff_seconds: int = 300
    backoff_multiplier: float = 1.5
    max_input_queue_size: int = 5
    max_output_queue_size: int = 5
    max_stream_queue_size: int = 10
    timezone: str = "UTC"


@dataclass
class MockStorageConfig:
    """Mock storage config for testing."""

    worktree_dir: Path | None = None

    def __post_init__(self):
        if self.worktree_dir is None:
            self.worktree_dir = Path(tempfile.mkdtemp()) / "worktrees"


@dataclass
class MockConfig:
    """Mock config for testing."""

    runtime: MockRuntimeConfig | None = None
    storage: MockStorageConfig | None = None

    def __post_init__(self):
        if self.runtime is None:
            self.runtime = MockRuntimeConfig()
        if self.storage is None:
            self.storage = MockStorageConfig()


class TestBoundedQueues(unittest.IsolatedAsyncioTestCase):
    """Test bounded queue behavior."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        from app.progenitor.runtime.loop import ProgenitorLoop

        self.config = MockConfig()
        self.state_manager = MagicMock()
        self.state_manager.get_schedule = AsyncMock(return_value=[])
        self.state_manager.remove_schedule_entry = AsyncMock(return_value=True)
        self.state_manager.update_schedule_entry = AsyncMock(return_value=True)
        self.state_manager.state_dir = Path(tempfile.mkdtemp())

        self.graph = MagicMock()

        self.loop = ProgenitorLoop(
            graph=self.graph,
            state_manager=self.state_manager,
            config=cast(Any, self.config),
        )

    async def test_input_queue_is_bounded(self):
        """Test that input queue has a max size."""
        assert self.loop._input_queue.maxsize == 5

    async def test_output_queue_is_bounded(self):
        """Test that output queue has a max size."""
        assert self.loop._output_queue.maxsize == 5

    async def test_stream_queue_is_bounded(self):
        """Test that stream queue has a max size."""
        assert self.loop._stream_queue.maxsize == 10

    async def test_enqueue_input_success(self):
        """Test successful input enqueue."""
        result = await self.loop.enqueue_input("test message")
        assert result is True
        assert self.loop._input_queue.qsize() == 1

    async def test_enqueue_input_stores_queued_input_wrapper(self):
        """Enqueued items should carry both content and enqueue timing."""
        from app.progenitor.runtime.loop import QueuedInput

        await self.loop.enqueue_input("wrapped message")
        queued = self.loop._input_queue.get_nowait()

        assert isinstance(queued, QueuedInput)
        assert queued.content == "wrapped message"
        assert queued.enqueue_time > 0

    async def test_get_input_records_wait_time(self):
        """Dequeuing input should capture how long the message waited."""
        await self.loop.enqueue_input("timed message")
        await asyncio.sleep(0.05)

        result = await self.loop._get_input(timeout=1.0)

        assert result == "timed message"
        assert self.loop._current_input_wait_seconds >= 0.0
        assert self.loop._current_input_wait_seconds < 10.0

    async def test_get_input_records_pending_count(self):
        """Dequeuing input should record how many messages remain queued."""
        for index in range(3):
            await self.loop.enqueue_input(f"message {index}")

        assert await self.loop._get_input(timeout=1.0) == "message 0"
        assert self.loop._current_input_pending_count == 2

        assert await self.loop._get_input(timeout=1.0) == "message 1"
        assert self.loop._current_input_pending_count == 1

        assert await self.loop._get_input(timeout=1.0) == "message 2"
        assert self.loop._current_input_pending_count == 0

    async def test_enqueue_input_with_timeout(self):
        """Test enqueue with timeout when queue is full."""
        # Fill the queue
        for i in range(5):
            await self.loop.enqueue_input(f"message {i}")

        # Queue should now be full
        assert self.loop._input_queue.full()

        # Try to enqueue with short timeout - should fail
        result = await self.loop.enqueue_input("overflow", timeout=0.1)
        assert result is False

    async def test_enqueue_input_non_blocking_raises_on_full(self):
        """Test non-blocking enqueue raises QueueFull when full."""
        # Fill the queue
        for i in range(5):
            await self.loop.enqueue_input(f"message {i}")

        # Non-blocking mode (timeout=0) should raise
        with pytest.raises(asyncio.QueueFull):
            await self.loop.enqueue_input("overflow", timeout=0)

    async def test_send_output_drops_on_full(self):
        """Test that output drops messages when queue is full."""
        # Fill the output queue
        for i in range(5):
            self.loop._output_queue.put_nowait(f"output {i}")

        # Should not raise, just log warning and drop
        await self.loop._send_output("overflow message")

        # Queue size should still be 5
        assert self.loop._output_queue.qsize() == 5

    async def test_send_stream_chunk_drops_on_full(self):
        """Test that stream chunks are dropped when queue is full."""
        # Fill the stream queue
        for i in range(10):
            self.loop._stream_queue.put_nowait({"content": f"chunk {i}"})

        # Should not raise, just log warning and drop
        await self.loop.stream.send_stream_chunk("overflow chunk")

        # Queue size should still be 10
        assert self.loop._stream_queue.qsize() == 10

    async def test_get_queue_stats(self):
        """Test queue statistics reporting."""
        # Add some items
        await self.loop.enqueue_input("msg1")
        await self.loop.enqueue_input("msg2")
        self.loop._output_queue.put_nowait("out1")

        stats = self.loop.get_queue_stats()

        assert stats["input_queue"]["size"] == 2
        assert stats["input_queue"]["maxsize"] == 5
        assert stats["input_queue"]["full"] is False

        assert stats["output_queue"]["size"] == 1
        assert stats["output_queue"]["maxsize"] == 5
        assert stats["output_queue"]["full"] is False

        assert stats["stream_queue"]["size"] == 0
        assert stats["stream_queue"]["maxsize"] == 10
        assert stats["stream_queue"]["full"] is False

    async def test_get_queue_stats_when_full(self):
        """Test queue stats when queues are full."""
        # Fill input queue
        for i in range(5):
            await self.loop.enqueue_input(f"msg {i}")

        stats = self.loop.get_queue_stats()

        assert stats["input_queue"]["full"] is True

    async def test_scheduler_enqueues_all_due_items(self):
        """Scheduler should enqueue every due task in a single poll cycle."""
        scheduler = MagicMock()
        scheduler.get_due_items.return_value = [
            {
                "id": "sched-1",
                "message": "first task",
                "cron_expression": "0 9 * * *",
            },
            {
                "id": "sched-2",
                "message": "second task",
                "cron_expression": "0 10 5 5 *",
            },
        ]
        scheduler.is_one_shot_expression.side_effect = [False, True]

        self.state_manager.get_schedule.return_value = scheduler.get_due_items.return_value

        count = await self.loop.scheduled_work.check_and_enqueue(scheduler)

        assert count == 2
        assert self.loop._input_queue.qsize() == 2
        self.state_manager.update_schedule_entry.assert_awaited_once()
        self.state_manager.remove_schedule_entry.assert_awaited_once_with("sched-2")

    async def test_enqueue_input_none_timeout_waits_forever(self):
        """Test that timeout=None waits indefinitely (until space available)."""
        # Fill the queue
        for i in range(5):
            await self.loop.enqueue_input(f"message {i}")

        # Start a task that will add with no timeout
        async def slow_enqueue():
            return await self.loop.enqueue_input("delayed", timeout=None)

        # Start enqueue task
        task = asyncio.create_task(slow_enqueue())

        # Give it a moment
        await asyncio.sleep(0.1)

        # Task should still be pending (waiting for space)
        assert not task.done()

        # Free up space
        await self.loop._input_queue.get()

        # Now task should complete
        await asyncio.sleep(0.1)
        assert task.done()
        result = await task
        assert result is True

    async def test_scheduler_dispatches_one_off_task(self):
        """Due one-off-like cron tasks should enqueue input and be removed."""
        from datetime import timedelta

        from app.progenitor.runtime.scheduler import Scheduler
        from app.progenitor.runtime.time_utils import utcnow

        now = utcnow().replace(microsecond=0)
        due_time = (now - timedelta(minutes=1)).isoformat()
        self.state_manager.get_schedule = AsyncMock(
            return_value=[
                {
                    "id": "sched_one_off",
                    "message": "say hello",
                    "cron_expression": f"{now.minute} {now.hour} {now.day} {now.month} *",
                    "created_at": due_time,
                    "last_run": None,
                }
            ]
        )
        self.loop.enqueue_input = AsyncMock(return_value=True)

        fired = await self.loop.scheduled_work.check_and_enqueue(Scheduler("UTC"))

        assert fired == 1
        self.loop.enqueue_input.assert_awaited_once()
        self.state_manager.remove_schedule_entry.assert_awaited_once_with("sched_one_off")

    async def test_scheduler_dispatches_recurring_task(self):
        """Due recurring tasks should enqueue input and advance schedule."""
        from datetime import timedelta

        from app.progenitor.runtime.scheduler import Scheduler
        from app.progenitor.runtime.time_utils import utcnow

        due_time = (utcnow() - timedelta(minutes=10)).replace(microsecond=0).isoformat()
        self.state_manager.get_schedule = AsyncMock(
            return_value=[
                {
                    "id": "sched_recurring",
                    "message": "check status",
                    "cron_expression": "*/5 * * * *",
                    "created_at": due_time,
                    "last_run": None,
                }
            ]
        )
        self.loop.enqueue_input = AsyncMock(return_value=True)

        fired = await self.loop.scheduled_work.check_and_enqueue(Scheduler("UTC"))

        assert fired == 1
        self.loop.enqueue_input.assert_awaited_once()
        self.state_manager.update_schedule_entry.assert_awaited_once()


class TestBoundedQueueConfiguration(unittest.IsolatedAsyncioTestCase):
    """Test queue configuration from settings."""

    async def test_custom_queue_sizes(self):
        """Test that custom queue sizes are respected."""
        from app.progenitor.runtime.loop import ProgenitorLoop

        config = MockConfig(
            runtime=MockRuntimeConfig(
                max_input_queue_size=50, max_output_queue_size=25, max_stream_queue_size=500
            )
        )

        state_manager = MagicMock()
        state_manager.state_dir = Path(tempfile.mkdtemp())

        loop = ProgenitorLoop(
            graph=MagicMock(),
            state_manager=state_manager,
            config=cast(Any, config),
        )

        assert loop._input_queue.maxsize == 50
        assert loop._output_queue.maxsize == 25
        assert loop._stream_queue.maxsize == 500


class TestConfigQueueSettings(unittest.TestCase):
    """Test RuntimeConfig queue settings."""

    def test_default_queue_sizes(self):
        """Test default queue sizes in RuntimeConfig."""
        from app.progenitor.config import RuntimeConfig

        config = RuntimeConfig()

        assert config.max_input_queue_size == 100
        assert config.max_output_queue_size == 100
        assert config.max_stream_queue_size == 1000

    def test_queue_sizes_from_env(self):
        """Test loading queue sizes from environment."""
        from app.progenitor.config import RuntimeConfig

        with patch.dict(
            "os.environ",
            {
                "PROGENITOR_MAX_INPUT_QUEUE_SIZE": "200",
                "PROGENITOR_MAX_OUTPUT_QUEUE_SIZE": "150",
                "PROGENITOR_MAX_STREAM_QUEUE_SIZE": "2000",
            },
        ):
            config = RuntimeConfig.from_env()

            assert config.max_input_queue_size == 200
            assert config.max_output_queue_size == 150
            assert config.max_stream_queue_size == 2000

    def test_queue_sizes_from_file_config(self):
        """Test loading queue sizes from file config."""
        from app.progenitor.config import RuntimeConfig

        file_config = {
            "max_input_queue_size": 75,
            "max_output_queue_size": 60,
            "max_stream_queue_size": 800,
        }

        config = RuntimeConfig.from_env(file_config=file_config)

        assert config.max_input_queue_size == 75
        assert config.max_output_queue_size == 60
        assert config.max_stream_queue_size == 800


class TestReasoningTraceExtraction(unittest.TestCase):
    """Tests for runtime reasoning extraction helpers."""

    def test_extracts_from_response_metadata(self):
        from app.progenitor.runtime.graph_results import extract_reasoning_trace

        msg = SimpleNamespace(response_metadata={"reasoning": {"summary": "done"}})
        trace = extract_reasoning_trace(msg)
        assert trace == "done"

    def test_extracts_summary_effort_schema(self):
        from app.progenitor.runtime.graph_results import extract_reasoning_trace

        msg = SimpleNamespace(response_metadata={"effort": "medium", "summary": "detailed"})
        trace = extract_reasoning_trace(msg)
        assert trace == "detailed"

    def test_extracts_from_content_parts(self):
        from app.progenitor.runtime.graph_results import extract_reasoning_trace

        msg = {
            "role": "assistant",
            "content": [{"type": "reasoning", "text": "step-by-step"}],
        }
        assert extract_reasoning_trace(msg) == "step-by-step"

    def test_extract_message_text_from_string_content(self):
        from app.progenitor.runtime.graph_results import extract_message_text

        msg = {"role": "assistant", "content": "hello there"}
        assert extract_message_text(msg) == "hello there"

    def test_extract_message_text_from_list_blocks(self):
        from app.progenitor.runtime.graph_results import extract_message_text

        msg = {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "one"},
                {"type": "text", "text": "two"},
            ],
        }
        assert extract_message_text(msg) == "one\ntwo"

    def test_finds_last_assistant_message(self):
        from app.progenitor.runtime.graph_results import get_last_ai_message

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "response_metadata": {"thinking": "brief"}},
        ]
        msg = get_last_ai_message(messages)
        assert isinstance(msg, dict)
        assert msg["role"] == "assistant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
