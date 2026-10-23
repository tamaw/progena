"""Tests for bounded queue functionality in ProgenaLoop."""

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime


@dataclass
class MockRuntimeConfig:
    """Mock runtime config for testing."""

    idle_backoff_seconds: int = 30
    mcp_refresh_seconds: int = 900
    plugin_update_check_seconds: int = 86400
    registry_refresh_seconds: int = 900
    repo_scan_seconds: int = 3600
    max_input_queue_size: int = 5
    max_output_queue_size: int = 5
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
        from src.progena.runtime.heartbeat import ProgenaLoop

        self.config = MockConfig()
        self.state_manager = MagicMock()
        self.state_manager.get_schedule = AsyncMock(return_value={})
        self.state_manager.remove_schedule_entry = AsyncMock(return_value=True)
        self.state_manager.update_schedule_entry = AsyncMock(return_value=True)
        self.state_manager.upsert_schedule_run = AsyncMock(return_value=None)
        self.state_manager.update_schedule_run = AsyncMock(return_value=True)
        self.state_manager.log_schedule_event = AsyncMock(return_value=None)
        self.state_manager.state_dir = Path(tempfile.mkdtemp())

        self.graph = MagicMock()

        self.loop = ProgenaLoop(
            graph=self.graph,
            state_manager=self.state_manager,
            config=cast(Any, self.config),
        )

    async def test_enqueue_input_success(self):
        """Test successful input enqueue."""
        result = await self.loop.queues.enqueue_input("test message")
        assert result is True
        assert self.loop.queues.input_queue.qsize() == 1

    async def test_enqueue_input_stores_queued_input_wrapper(self):
        """Enqueued items should carry both content and enqueue timing."""
        from src.progena.runtime.heartbeat import QueuedInput

        await self.loop.queues.enqueue_input("wrapped message")
        queued = self.loop.queues.input_queue.get_nowait()

        assert isinstance(queued, QueuedInput)
        assert queued.content == "wrapped message"
        assert queued.enqueue_time > 0

    async def test_get_input_records_wait_time(self):
        """Dequeuing input should capture how long the message waited."""
        await self.loop.queues.enqueue_input("timed message")
        await asyncio.sleep(0.05)

        result = await self.loop.queues.get_input(timeout=1.0)

        assert result == "timed message"
        assert self.loop.queues.current_input_wait_seconds >= 0.0
        assert self.loop.queues.current_input_wait_seconds < 10.0

    async def test_get_input_records_pending_count(self):
        """Dequeuing input should record how many messages remain queued."""
        for index in range(3):
            await self.loop.queues.enqueue_input(f"message {index}")

        assert await self.loop.queues.get_input(timeout=1.0) == "message 0"
        assert self.loop.queues.current_input_pending_count == 2

        assert await self.loop.queues.get_input(timeout=1.0) == "message 1"
        assert self.loop.queues.current_input_pending_count == 1

        assert await self.loop.queues.get_input(timeout=1.0) == "message 2"
        assert self.loop.queues.current_input_pending_count == 0

    async def test_enqueue_input_with_timeout(self):
        """Test enqueue with timeout when queue is full."""
        # Fill the queue
        for i in range(5):
            await self.loop.queues.enqueue_input(f"message {i}")

        # Queue should now be full
        assert self.loop.queues.input_queue.full()

        # Try to enqueue with short timeout - should fail
        result = await self.loop.queues.enqueue_input("overflow", timeout=0.1)
        assert result is False

    async def test_enqueue_input_non_blocking_raises_on_full(self):
        """Test non-blocking enqueue raises QueueFull when full."""
        # Fill the queue
        for i in range(5):
            await self.loop.queues.enqueue_input(f"message {i}")

        # Non-blocking mode (timeout=0) should raise
        with pytest.raises(asyncio.QueueFull):
            await self.loop.queues.enqueue_input("overflow", timeout=0)

    async def test_get_queue_stats(self):
        """Test queue statistics reporting."""
        await self.loop.queues.enqueue_input("msg1")
        await self.loop.queues.enqueue_input("msg2")

        stats = self.loop.queues.get_queue_stats()

        assert stats["input_queue"]["size"] == 2
        assert stats["input_queue"]["maxsize"] == 5
        assert stats["input_queue"]["full"] is False

    async def test_get_queue_stats_when_full(self):
        """Test queue stats when queues are full."""
        # Fill input queue
        for i in range(5):
            await self.loop.queues.enqueue_input(f"msg {i}")

        stats = self.loop.queues.get_queue_stats()

        assert stats["input_queue"]["full"] is True

    async def test_scheduler_enqueues_all_due_items(self):
        """Scheduler should enqueue every due task in a single poll cycle."""
        from src.progena.orchestration.nodes.schedule import scheduler_tick_node
        from src.progena.utils import utcnow

        due_time = utcnow().replace(microsecond=0).isoformat()
        due_items = {
            "sched-1": {
                "id": "sched-1",
                "message": "first task",
                "scheduled_at": due_time,
            },
            "sched-2": {
                "id": "sched-2",
                "message": "second task",
                "scheduled_at": due_time,
            },
        }

        self.state_manager.get_schedule.return_value = due_items

        self.loop.heartbeat_context.scheduler_enabled = True
        self.loop.heartbeat_context.scheduler_poll_seconds = 0.1
        self.loop.heartbeat_context.due_maintenance = {"schedule_tick"}
        updates = await scheduler_tick_node(
            cast(Any, {}),
            Runtime(context=self.loop.heartbeat_context),
        )
        count = int(updates.get("scheduled_enqueued_count") or 0)

        assert count == 2
        assert self.loop.queues.input_queue.qsize() == 2
        assert self.state_manager.update_schedule_entry.await_count == 2
        updated_ids = [
            call.args[0] for call in self.state_manager.update_schedule_entry.await_args_list
        ]
        assert updated_ids == ["sched-1", "sched-2"]
        self.state_manager.remove_schedule_entry.assert_not_awaited()

    async def test_enqueue_input_none_timeout_waits_forever(self):
        """Test that timeout=None waits indefinitely (until space available)."""
        # Fill the queue
        for i in range(5):
            await self.loop.queues.enqueue_input(f"message {i}")

        # Start a task that will add with no timeout
        async def slow_enqueue():
            return await self.loop.queues.enqueue_input("delayed", timeout=None)

        # Start enqueue task
        task = asyncio.create_task(slow_enqueue())

        # Give it a moment
        await asyncio.sleep(0.1)

        # Task should still be pending (waiting for space)
        assert not task.done()

        # Free up space
        await self.loop.queues.input_queue.get()

        # Now task should complete
        await asyncio.sleep(0.1)
        assert task.done()
        result = await task
        assert result is True

    async def test_scheduler_dispatches_one_off_task(self):
        """Due one-off tasks should enqueue input and be marked completed."""
        from datetime import timedelta

        from src.progena.orchestration.nodes.schedule import scheduler_tick_node
        from src.progena.utils import utcnow

        now = utcnow().replace(microsecond=0)
        due_time = (now - timedelta(minutes=1)).isoformat()
        self.state_manager.get_schedule = AsyncMock(
            return_value={
                "cron_one_off": {
                    "id": "cron_one_off",
                    "message": "say hello",
                    "scheduled_at": due_time,
                    "created_at": due_time,
                    "last_run": None,
                }
            }
        )
        enqueue_input = AsyncMock(return_value=True)
        self.loop.heartbeat_context.queues.enqueue_input = enqueue_input

        self.loop.heartbeat_context.scheduler_enabled = True
        self.loop.heartbeat_context.scheduler_poll_seconds = 0.1
        self.loop.heartbeat_context.due_maintenance = {"schedule_tick"}
        updates = await scheduler_tick_node(
            cast(Any, {}),
            Runtime(context=self.loop.heartbeat_context),
        )
        fired = int(updates.get("scheduled_enqueued_count") or 0)

        assert fired == 1
        enqueue_input.assert_awaited_once()
        self.state_manager.update_schedule_entry.assert_awaited_once()
        update_args = self.state_manager.update_schedule_entry.await_args.args
        assert update_args[0] == "cron_one_off"
        assert update_args[1]["paused"] is True
        assert update_args[1]["enabled"] is False
        assert update_args[1]["last_run_status"] == "running"
        self.state_manager.upsert_schedule_run.assert_awaited_once()
        self.state_manager.log_schedule_event.assert_awaited_once()

    async def test_scheduler_dispatches_recurring_task(self):
        """Due recurring tasks should enqueue input and advance schedule."""
        from datetime import timedelta

        from src.progena.orchestration.nodes.schedule import scheduler_tick_node
        from src.progena.utils import utcnow

        due_time = (utcnow() - timedelta(minutes=10)).replace(microsecond=0).isoformat()
        self.state_manager.get_schedule = AsyncMock(
            return_value={
                "sched_recurring": {
                    "id": "sched_recurring",
                    "message": "check status",
                    "cron_expression": "*/5 * * * *",
                    "created_at": due_time,
                    "last_run": None,
                }
            }
        )
        enqueue_input = AsyncMock(return_value=True)
        self.loop.heartbeat_context.queues.enqueue_input = enqueue_input

        self.loop.heartbeat_context.scheduler_enabled = True
        self.loop.heartbeat_context.scheduler_poll_seconds = 0.1
        self.loop.heartbeat_context.due_maintenance = {"schedule_tick"}
        updates = await scheduler_tick_node(
            cast(Any, {}),
            Runtime(context=self.loop.heartbeat_context),
        )
        fired = int(updates.get("scheduled_enqueued_count") or 0)

        assert fired == 1
        enqueue_input.assert_awaited_once()
        self.state_manager.update_schedule_entry.assert_awaited_once()
        self.state_manager.upsert_schedule_run.assert_awaited_once()

    async def test_scheduled_completion_updates_run_and_schedule(self):
        from src.progena.schedules.dispatch import emit_scheduled_completion

        queues = self.loop.queues
        state = {
            "is_scheduled_input": True,
            "scheduled_schedule_id": "cron_test123",
            "scheduled_run_id": "schedrun_123",
            "scheduled_message": "say hello",
            "last_cycle_response": "hello",
        }

        updates = await emit_scheduled_completion(state, self.state_manager, queues)

        assert updates["scheduled_result_status"] == "completed"
        self.state_manager.update_schedule_run.assert_awaited_once()
        self.state_manager.update_schedule_entry.assert_awaited_once()

    async def test_cycle_setup_marks_schedule_run_running(self):
        from src.progena.orchestration.nodes.intake import cycle_setup_node

        state = {
            "queued_input": {
                "content": "[Scheduled Task]\nhello",
                "metadata": {
                    "thread_id": "cron_test123_abcd1234",
                    "schedule_id": "cron_test123",
                    "schedule_run_id": "schedrun_123",
                },
            },
            "thread_id": "thread_main",
            "cycle_count": 0,
        }

        updates = await cycle_setup_node(
            cast(Any, state),
            Runtime(context=self.loop.heartbeat_context),
        )

        assert updates["requested_thread_id"] == "cron_test123_abcd1234"
        assert updates["scheduled_run_id"] == "schedrun_123"
        self.state_manager.update_schedule_run.assert_awaited_once()


class TestReasoningTraceExtraction(unittest.TestCase):
    """Tests for runtime reasoning extraction helpers."""

    def test_extracts_from_response_metadata(self):
        from src.progena.sessions.messages import extract_reasoning_trace

        msg = SimpleNamespace(response_metadata={"reasoning": {"summary": "done"}})
        trace = extract_reasoning_trace(msg)
        assert trace == "done"

    def test_extracts_summary_effort_schema(self):
        from src.progena.sessions.messages import extract_reasoning_trace

        msg = SimpleNamespace(response_metadata={"effort": "medium", "summary": "detailed"})
        trace = extract_reasoning_trace(msg)
        assert trace == "detailed"

    def test_extracts_from_content_parts(self):
        from src.progena.sessions.messages import extract_reasoning_trace

        msg = {
            "role": "assistant",
            "content": [{"type": "reasoning", "text": "step-by-step"}],
        }
        assert extract_reasoning_trace(msg) == "step-by-step"

    def test_extract_message_text_from_string_content(self):
        from src.progena.sessions.messages import extract_message_text

        msg = {"role": "assistant", "content": "hello there"}
        assert extract_message_text(msg) == "hello there"

    def test_extract_message_text_from_list_blocks(self):
        from src.progena.sessions.messages import extract_message_text

        msg = {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "one"},
                {"type": "text", "text": "two"},
            ],
        }
        assert extract_message_text(msg) == "one\ntwo"

    def test_finds_last_assistant_message(self):
        from src.progena.sessions.messages import get_last_ai_message

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "response_metadata": {"thinking": "brief"}},
        ]
        msg = get_last_ai_message(messages)
        assert isinstance(msg, dict)
        assert msg["role"] == "assistant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
