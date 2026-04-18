"""Tests for the builder background queue worker."""

import importlib
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("AGENT_NAME", "builder")

from app.agents.builder import queue_worker
from app.agents.builder.agent import BuildOutcome
from app.agents.builder.client import BuildPlan, PlannedBuild
from app.agents.builder.queue_worker import BuilderTaskQueue, _QueuedTask


class _FakeService:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.planned = PlannedBuild(
            plan=BuildPlan(
                task_name="Queued Task",
                work_item_id="queued-task",
                branch_name="progenitor/queued-task",
                description="Queue test",
                copilot_goal="Queue behavior",
                success_criteria=["Task queues"],
                validation_targets=["pytest"],
            ),
            reasoning="Queue test plan.",
            planned_at_utc="2026-01-01T00:00:00Z",
        )

    async def cancel_task(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return task_id == "running-task"

    async def plan_task(self, request_text: str, context: dict[str, object]) -> PlannedBuild:
        del request_text
        del context
        return self.planned

    async def emit_hook(self, event: str, context: object) -> None:
        del event
        del context

    async def run_task(
        self,
        task_id: str,
        request_text: str,
        context: dict[str, object],
        *,
        on_planned: Any | None = None,
    ) -> BuildOutcome:
        del task_id
        del request_text
        del context
        if on_planned is not None:
            await on_planned(self.planned)
        return BuildOutcome(
            result_text="Done. PR: https://github.com/org/repo/pull/1",
            route="build",
            planned_build=self.planned,
            commit_hash="abc1234",
            pr_url="https://github.com/org/repo/pull/1",
            phase_completed="pr",
        )


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
    monkeypatch.delenv("PROGENITOR_LOG_DIR", raising=False)
    monkeypatch.setenv("AGENT_NAME", "builder")

    reloaded = importlib.reload(queue_worker)
    try:
        expected = Path(__file__).resolve().parents[3] / "logs" / "builder" / "builder_tasks.jsonl"
        assert reloaded.get_task_log_path() == expected
    finally:
        importlib.reload(queue_worker)


def test_task_log_path_uses_agent_name_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PROGENITOR_LOG_DIR", raising=False)
    monkeypatch.setenv("AGENT_NAME", "custom_builder")

    reloaded = importlib.reload(queue_worker)
    try:
        expected = (
            Path(__file__).resolve().parents[3]
            / "logs"
            / "custom_builder"
            / "custom_builder_tasks.jsonl"
        )
        assert reloaded.get_task_log_path() == expected
    finally:
        importlib.reload(queue_worker)


def test_task_log_path_requires_agent_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_NAME", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_NAME must be set"):
        importlib.reload(queue_worker).get_task_log_path()

    monkeypatch.setenv("AGENT_NAME", "builder")
    importlib.reload(queue_worker)
