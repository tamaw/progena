"""Tests for the builder background queue worker."""

import importlib

import pytest

from app.agents.builder_progenitor import queue_worker
from app.agents.builder_progenitor.queue_worker import BuilderTaskQueue, _QueuedTask


class _FakeService:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_task(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return task_id == "running-task"


@pytest.mark.asyncio
async def test_cancel_marks_queued_task_without_hitting_service():
    service = _FakeService()
    queue = BuilderTaskQueue(service)  # type: ignore[arg-type]
    queued = _QueuedTask("queued-task", "ctx", "req", {})

    await queue.enqueue(queued)

    cancelled = await queue.cancel("queued-task")

    assert cancelled is True
    assert "queued-task" in queue._cancelled_task_ids
    assert service.cancelled == []


@pytest.mark.asyncio
async def test_cancel_delegates_to_service_for_running_task():
    service = _FakeService()
    queue = BuilderTaskQueue(service)  # type: ignore[arg-type]

    cancelled = await queue.cancel("running-task")

    assert cancelled is True
    assert service.cancelled == ["running-task"]


def test_task_log_path_defaults_to_progenitor_log_dir(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BUILDER_TASK_LOG", raising=False)
    monkeypatch.setenv("PROGENITOR_LOG_DIR", "/app/logs/builder_progenitor")

    reloaded = importlib.reload(queue_worker)
    try:
        assert reloaded.TASK_LOG_PATH.as_posix() == "/app/logs/builder_progenitor/builder_progenitor_tasks.jsonl"
    finally:
        importlib.reload(queue_worker)
