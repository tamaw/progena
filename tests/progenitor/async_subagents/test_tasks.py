"""Tests for async-subagent task helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.progenitor.async_subagents.models import AsyncSubagentTaskRecord, AsyncSubagentTaskStatus
from src.progenitor.async_subagents.tasks import refresh_tracked_async_subagent_task


class TestRefreshTrackedAsyncSubagentTask:
    async def test_interrupted_run_is_surfaced_but_not_completed(self):
        record = AsyncSubagentTaskRecord(
            task_id="thread-1",
            agent_name="test-agent",
            assistant_id="a1",
            agent_url="http://test",
            run_id="run-1",
            status=AsyncSubagentTaskStatus.RUNNING,
            summary="Working",
        )

        class FakeClient:
            class runs:
                @staticmethod
                async def get(thread_id, run_id):
                    return {"status": "interrupted", "error": None}

        state_manager = FakeStateManager({"thread-1": record})
        with patch("src.progenitor.async_subagents.tasks.get_client", return_value=FakeClient()):
            refreshed = await refresh_tracked_async_subagent_task(state_manager, "thread-1")
        assert refreshed.status == "interrupted"
        assert refreshed.summary == "Awaiting human approval"
        assert refreshed.completed_at is None
        assert not refreshed.is_terminal


class FakeStateManager:
    def __init__(self, tasks):
        self._tasks = dict(tasks)

    async def get_async_subagent_task(self, task_id):
        return self._tasks.get(task_id)

    async def update_async_subagent_task(self, task_id, updates):
        task = self._tasks.get(task_id)
        if task is not None:
            for key, value in updates.items():
                setattr(task, key, value)
