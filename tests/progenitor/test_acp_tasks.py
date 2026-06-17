"""Tests for tracked ACP run dispatch (blocking + background-tracked)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.progenitor.acp import tasks as acp_tasks
from app.progenitor.acp.runner import ACPRunResult
from app.progenitor.state.models import ACPTaskRecord, ACPTaskStatus


class _FakeStateManager:
    """In-memory stand-in for the parts of StateManager the dispatch path uses."""

    def __init__(self) -> None:
        self._tasks: dict[str, ACPTaskRecord] = {}

    async def upsert_acp_task(self, task: ACPTaskRecord) -> None:
        self._tasks[task.task_id] = task

    async def get_acp_task(self, task_id: str) -> ACPTaskRecord | None:
        return self._tasks.get(task_id)

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        existing = self._tasks.get(task_id)
        if existing is None:
            return False
        payload = existing.model_dump()
        payload.update(updates)
        self._tasks[task_id] = ACPTaskRecord(**payload)
        return True


def _stub_runner(result: ACPRunResult, monkeypatch):
    class _StubRunner:
        async def run(self, *_a: Any, **_k: Any) -> ACPRunResult:
            return result

    monkeypatch.setattr(acp_tasks, "_runner_for", lambda *a, **k: _StubRunner())


async def test_blocking_dispatch_returns_terminal_record(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGENITOR_ACP_WORKSPACE", str(tmp_path / "ws"))
    _stub_runner(ACPRunResult(success=True, output="all done", stop_reason="end_turn"), monkeypatch)
    sm = _FakeStateManager()

    response, record = await acp_tasks.dispatch_tracked_acp_task(
        state_manager=sm,
        agent_name="github_copilot",
        agent_config={"acp": {"command": "copilot", "profile": "copilot"}},
        request_text="do it",
        blocking=True,
    )
    assert response["success"] is True
    assert record.status == ACPTaskStatus.COMPLETED.value
    assert record.output == "all done"
    # A fresh per-run sandbox was provisioned.
    assert record.cwd and Path(record.cwd).is_dir()


async def test_dispatch_fails_fast_when_acp_config_missing(monkeypatch, tmp_path):
    """A registry entry without an 'acp' config must not silently spawn Copilot."""
    monkeypatch.setenv("PROGENITOR_ACP_WORKSPACE", str(tmp_path / "ws"))
    sm = _FakeStateManager()

    with pytest.raises(ValueError, match="missing its 'acp' configuration"):
        await acp_tasks.dispatch_tracked_acp_task(
            state_manager=sm,
            agent_name="github_copilot",
            agent_config={"name": "github_copilot"},
            request_text="do it",
            blocking=True,
        )


async def test_background_dispatch_completes_via_task(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGENITOR_ACP_WORKSPACE", str(tmp_path / "ws"))
    _stub_runner(
        ACPRunResult(success=False, output="", stop_reason="error", error="boom"), monkeypatch
    )
    sm = _FakeStateManager()

    response, record = await acp_tasks.dispatch_tracked_acp_task(
        state_manager=sm,
        agent_name="github_copilot",
        agent_config={"acp": {"command": "copilot", "profile": "copilot"}},
        request_text="do it",
        blocking=False,
    )
    # Returns immediately in the working state.
    assert record.status == ACPTaskStatus.WORKING.value
    assert response["task_id"] == record.task_id

    # Drain the background run and confirm it finalized the record.
    await asyncio.gather(*list(acp_tasks._background_runs))
    final = await sm.get_acp_task(record.task_id)
    assert final is not None
    assert final.status == ACPTaskStatus.FAILED.value
    assert final.last_error == "boom"
