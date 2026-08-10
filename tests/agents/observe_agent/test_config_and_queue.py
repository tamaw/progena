import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

from app.agents.observe_agent import queue_worker
from app.agents.observe_agent.config import ObserveConfig
from app.agents.observe_agent.prompts import load_prompt
from app.agents.observe_agent.queue_worker import (
    ObserveTaskQueue,
    TaskRecord,
    _QueuedTask,
    append_task_record,
    sync_wait_for_completion,
)


def test_observe_config_prefers_agent_model(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "gpt-5-mini")
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.1")
    monkeypatch.setenv("PROGENITOR_LOG_DIR", "/tmp/logs")
    monkeypatch.delenv("PROGENITOR_DATA_DIR", raising=False)
    monkeypatch.delenv("PROGENITOR_PROMPTS_DIR", raising=False)
    # Asserts the built-in default below, so the overrides that would displace
    # it have to go too -- a developer .env commonly sets PROGENITOR_WORKTREE_DIR.
    monkeypatch.delenv("PROGENITOR_WORKTREE_DIR", raising=False)
    monkeypatch.delenv("PROGENITOR_WORKTREES_ROOT", raising=False)
    config = ObserveConfig.load()
    repo_root = Path(__file__).resolve().parents[3]

    assert config.llm.get_model() == "gpt-5-mini"
    assert config.storage.data_dir == repo_root / "data"
    assert config.storage.state_dir == repo_root / "data" / "state"
    assert config.storage.log_dir == Path("/tmp/logs")
    assert config.storage.worktree_dir == repo_root / "worktrees"
    assert config.storage.prompts_dir == repo_root / "data" / "prompts"


def test_observe_task_log_path_defaults_to_repo_logs(monkeypatch) -> None:
    monkeypatch.delenv("PROGENITOR_LOG_DIR", raising=False)
    monkeypatch.setenv("AGENT_NAME", "observe_agent")

    reloaded = importlib.reload(queue_worker)
    try:
        expected = (
            Path(__file__).resolve().parents[3]
            / "logs"
            / "observe_agent"
            / "observe_agent_tasks.jsonl"
        )
        assert reloaded.get_task_log_path() == expected
    finally:
        importlib.reload(queue_worker)


def test_load_prompt_reads_observe_system_prompt() -> None:
    content = load_prompt("system.md")
    assert "You are Observe Agent" in content


def test_append_task_record_can_be_polled(tmp_path: Path) -> None:
    log_path = tmp_path / "observe_tasks.jsonl"
    append_task_record(
        TaskRecord(
            task_id="task-123",
            context_id="ctx-123",
            status="success",
            response_text="done",
            error=None,
            timestamp_utc="2026-03-30T00:00:00Z",
        ),
        log_path=log_path,
    )

    record = sync_wait_for_completion(
        "task-123", timeout_seconds=0.1, poll_interval=0.01, log_path=log_path
    )
    assert record is not None
    assert record["status"] == "success"
    assert record["response_text"] == "done"


@pytest.mark.asyncio
async def test_observe_queue_emits_working_and_completed(tmp_path: Path) -> None:
    log_path = tmp_path / "observe_tasks.jsonl"
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()

    service = AsyncMock()
    service.process = AsyncMock(return_value="Observe complete")

    queue = ObserveTaskQueue(service, log_path=log_path)
    queue.start()
    try:
        await queue.enqueue(
            _QueuedTask(
                task_id="observe-1",
                context_id="ctx-1",
                request_text="Inspect state",
                request_ctx={"a2a_task_id": "observe-1"},
                event_queue=event_queue,
            )
        )

        record = None
        for _ in range(30):
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion("observe-1", timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None
        assert record["status"] == "success"
        assert record["response_text"] == "Observe complete"
        states = [call.args[0].status.state for call in event_queue.enqueue_event.call_args_list]
        assert states == [TaskState.working, TaskState.completed]
    finally:
        await queue.stop()
