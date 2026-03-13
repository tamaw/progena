from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agents.builder_agent import queue_worker
from app.agents.builder_agent.queue_worker import (
    BuilderTaskQueue,
    TaskRecord,
    _QueuedTask,
    append_task_record,
    sync_wait_for_completion,
)
from app.agents.builder_agent.runtime.background_build import BuildResult, BuildStatus
from app.agents.builder_agent.runtime.time_utils import utcnow


def test_default_task_log_path_uses_agent_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUILDER_TASK_LOG", raising=False)
    monkeypatch.delenv("PROGENITOR_LOG_DIR", raising=False)
    monkeypatch.setenv("AGENT_NAME", "custom_builder")

    reloaded = importlib.reload(queue_worker)
    try:
        assert reloaded.TASK_LOG_PATH.as_posix() == "/app/logs/custom_builder/custom_builder_tasks.jsonl"
    finally:
        importlib.reload(queue_worker)


def test_append_task_record_and_poll(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    append_task_record(
        TaskRecord(
            task_id="task-1",
            status="success",
            phase_completed="pr",
            build_id="build-1",
            pr_url="https://github.com/org/repo/pull/1",
            timestamp_utc="2026-03-12T00:00:00Z",
        ),
        log_path,
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["build_id"] == "build-1"
    assert sync_wait_for_completion("task-1", timeout_seconds=0.0, log_path=log_path) == record


@pytest.mark.asyncio
async def test_queue_executes_and_logs_completion(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"

    async def handler(task_id: str, context_id: str, request_text: str, request_ctx: dict[str, str]) -> BuildResult:
        assert task_id == "task-2"
        assert context_id == "ctx-2"
        assert request_text == "Build something"
        assert request_ctx["a2a_task_id"] == "task-2"
        return BuildResult(
            build_id="build-2",
            status=BuildStatus.COMPLETED,
            task_description="build_agent: demo",
            started_at=utcnow(),
            completed_at=utcnow(),
            output_summary="done",
            pr_url="https://github.com/org/repo/pull/2",
        )

    queue = BuilderTaskQueue(handler, log_path=log_path)
    queue.start()
    try:
        await queue.enqueue(
            _QueuedTask(
                task_id="task-2",
                context_id="ctx-2",
                request_text="Build something",
                request_ctx={"a2a_task_id": "task-2"},
            )
        )

        record = None
        for _ in range(30):
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion("task-2", timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None
        assert record["status"] == "success"
        assert record["phase_completed"] == "pr"
        assert record["build_id"] == "build-2"
        assert record["pr_url"] == "https://github.com/org/repo/pull/2"
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_cancel_marks_queued_task_without_running_handler(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    handler = AsyncMock()
    queue = BuilderTaskQueue(handler, log_path=log_path)

    await queue.enqueue(
        _QueuedTask(
            task_id="queued-task",
            context_id="ctx-queued",
            request_text="Build later",
            request_ctx={},
        )
    )

    cancelled = await queue.cancel("queued-task")

    assert cancelled is True
    assert "queued-task" in queue._cancelled_task_ids
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_running_task_cancels_active_coroutine(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    started = asyncio.Event()
    released = asyncio.Event()

    async def handler(task_id: str, context_id: str, request_text: str, request_ctx: dict[str, str]) -> BuildResult:
        del task_id, context_id, request_text, request_ctx
        started.set()
        await released.wait()
        return BuildResult(
            build_id="build-running",
            status=BuildStatus.COMPLETED,
            task_description="build_agent: running",
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    queue = BuilderTaskQueue(handler, log_path=log_path)
    queue.start()
    try:
        await queue.enqueue(
            _QueuedTask(
                task_id="running-task",
                context_id="ctx-running",
                request_text="Build now",
                request_ctx={},
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2.0)

        cancelled = await queue.cancel("running-task")
        assert cancelled is True

        record = None
        for _ in range(30):
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion("running-task", timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None
        assert record["status"] == "cancelled"
        assert record["phase_completed"] == "cancelled"
    finally:
        released.set()
        await queue.stop()
