"""Integration test: fire-and-forget enqueue and atomic JSONL completion logging.

Verifies that:
1. The HTTP handler (BuilderExecutor.execute) returns immediately with a
   ``submitted`` state — it must not block on execution.
2. A ``BuilderTaskQueue`` worker picks up the queued task and, within 60 s,
   appends a JSONL record to the task log with the expected fields.
3. ``sync_wait_for_completion`` returns the record once it is present.
4. Atomic append correctness: concurrent writers do not corrupt the log.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("AGENT_NAME", "builder")

from app.agents.builder.agent import BuildOutcome
from app.agents.builder.client import BuildPlan, PlannedBuild
from app.agents.builder.queue_worker import (
    BuilderTaskQueue,
    TaskRecord,
    _QueuedTask,
    append_task_record,
    sync_wait_for_completion,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(task_id: str | None = None) -> _QueuedTask:
    return _QueuedTask(
        task_id=task_id or str(uuid.uuid4()),
        context_id=str(uuid.uuid4()),
        request_text="Implement fire-and-forget test task",
        request_ctx={
            "a2a_task_id": task_id or str(uuid.uuid4()),
            "a2a_context_id": str(uuid.uuid4()),
        },
    )


def _make_mock_service(response: str = "Done. PR: https://github.com/org/repo/pull/42") -> Any:
    svc = AsyncMock()
    planned_build = PlannedBuild(
        plan=BuildPlan(
            task_name="Fire And Forget Test",
            work_item_id="fire-and-forget-test",
            branch_name="progenitor/fire-and-forget-test",
            description="Exercise queue worker behavior",
            copilot_goal="Return a canned response",
            success_criteria=["Task completes"],
            validation_targets=["pytest"],
        ),
        reasoning="Mock planning result.",
        planned_at_utc="2026-01-01T00:00:00Z",
    )

    pr_match = re.search(r"https://github\.com/\S+/pull/\d+", response)
    pr_url = pr_match.group(0) if pr_match else None

    async def _run_task(
        task_id: str,
        request_text: str,
        context: dict[str, Any],
        *,
        on_planned: Any | None = None,
    ) -> BuildOutcome:
        del task_id
        del request_text
        del context
        if on_planned is not None:
            await on_planned(planned_build)
        return BuildOutcome(
            result_text=response,
            route="build",
            planned_build=planned_build,
            commit_hash=None,
            pr_url=pr_url,
            phase_completed="pr",
        )

    svc.process = AsyncMock(return_value=BuildOutcome(result_text=response))
    svc.plan_task = AsyncMock(return_value=planned_build)
    svc.run_task = AsyncMock(side_effect=_run_task)
    svc.emit_hook = AsyncMock(return_value=None)
    svc.cancel_task = AsyncMock(return_value=False)
    return svc


# ---------------------------------------------------------------------------
# Unit: atomic JSONL append
# ---------------------------------------------------------------------------


def test_append_task_record_creates_file_and_directory(tmp_path: Path) -> None:
    log_path = tmp_path / "subdir" / "tasks.jsonl"
    record = TaskRecord(
        task_id="t1",
        status="success",
        phase_completed="pr",
        commit_hash="abc1234",
        pr_url="https://github.com/org/repo/pull/1",
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    append_task_record(record, log_path)

    assert log_path.exists(), "log file must be created"
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["task_id"] == "t1"
    assert data["status"] == "success"
    assert data["phase_completed"] == "pr"
    assert data["commit_hash"] == "abc1234"
    assert data["pr_url"] == "https://github.com/org/repo/pull/1"
    assert data["timestamp_utc"] == "2026-01-01T00:00:00Z"


def test_append_task_record_required_keys(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    record = TaskRecord(
        task_id="t2",
        status="failure",
        phase_completed="failed",
        commit_hash=None,
        pr_url=None,
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    append_task_record(record, log_path)
    data = json.loads(log_path.read_text().strip())
    expected_keys = {
        "task_id",
        "status",
        "phase_completed",
        "commit_hash",
        "pr_url",
        "timestamp_utc",
    }
    assert expected_keys.issubset(data.keys())


def test_append_task_record_multiple_records(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    for i in range(5):
        append_task_record(
            TaskRecord(
                task_id=f"task-{i}",
                status="success",
                phase_completed="pr",
                commit_hash=None,
                pr_url=None,
                timestamp_utc="2026-01-01T00:00:00Z",
            ),
            log_path,
        )
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 5
    ids = [json.loads(ln)["task_id"] for ln in lines]
    assert ids == [f"task-{i}" for i in range(5)]


def test_concurrent_atomic_append(tmp_path: Path) -> None:
    """Multiple threads appending simultaneously must not corrupt records."""
    import threading

    log_path = tmp_path / "concurrent.jsonl"
    n = 20
    errors: list[Exception] = []

    def _write(i: int) -> None:
        try:
            append_task_record(
                TaskRecord(
                    task_id=f"c-{i}",
                    status="success",
                    phase_completed="pr",
                    commit_hash=None,
                    pr_url=None,
                    timestamp_utc="2026-01-01T00:00:00Z",
                ),
                log_path,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_write, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent append raised: {errors}"
    lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == n, f"Expected {n} records, got {len(lines)}"
    # Every line must be valid JSON
    for ln in lines:
        json.loads(ln)


# ---------------------------------------------------------------------------
# Unit: sync_wait_for_completion helper
# ---------------------------------------------------------------------------


def test_sync_wait_for_completion_finds_record(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    task_id = str(uuid.uuid4())
    append_task_record(
        TaskRecord(
            task_id=task_id,
            status="success",
            phase_completed="pr",
            commit_hash=None,
            pr_url=None,
            timestamp_utc="2026-01-01T00:00:00Z",
        ),
        log_path,
    )
    result = sync_wait_for_completion(task_id, timeout_seconds=2.0, log_path=log_path)
    assert result is not None
    assert result["task_id"] == task_id
    assert result["status"] == "success"


def test_sync_wait_for_completion_returns_none_on_timeout(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    result = sync_wait_for_completion(
        "nonexistent-task-id",
        timeout_seconds=0.5,
        poll_interval=0.1,
        log_path=log_path,
    )
    assert result is None


def test_sync_wait_for_completion_ignores_other_records(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    for i in range(3):
        append_task_record(
            TaskRecord(
                task_id=f"other-{i}",
                status="success",
                phase_completed="pr",
                commit_hash=None,
                pr_url=None,
                timestamp_utc="2026-01-01T00:00:00Z",
            ),
            log_path,
        )
    target = "target-task"
    append_task_record(
        TaskRecord(
            task_id=target,
            status="failure",
            phase_completed="failed",
            commit_hash=None,
            pr_url=None,
            timestamp_utc="2026-01-01T00:00:00Z",
        ),
        log_path,
    )
    result = sync_wait_for_completion(target, timeout_seconds=1.0, log_path=log_path)
    assert result is not None
    assert result["task_id"] == target
    assert result["status"] == "failure"


# ---------------------------------------------------------------------------
# Integration: BuilderTaskQueue executes task and writes JSONL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_queue_executes_and_logs_success(tmp_path: Path) -> None:
    """Worker must complete the task and append a success record within 10 s."""
    log_path = tmp_path / "tasks.jsonl"
    task_id = str(uuid.uuid4())
    service = _make_mock_service("All done. PR: https://github.com/org/repo/pull/99")

    queue = BuilderTaskQueue(service, log_path=log_path)
    queue.start()

    try:
        await queue.enqueue(_make_task(task_id))
        # Poll for the completion record (up to 10 s)
        record = None
        for _ in range(100):
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion(task_id, timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None, "JSONL record must appear after task completes"
        assert record["task_id"] == task_id
        assert record["status"] == "success"
        assert record["phase_completed"] == "pr"
        assert record["pr_url"] == "https://github.com/org/repo/pull/99"
        assert "timestamp_utc" in record
        assert record["timestamp_utc"].endswith("Z")
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_task_queue_logs_failure_on_exception(tmp_path: Path) -> None:
    """Worker must log a failure record when service.process raises."""
    log_path = tmp_path / "tasks.jsonl"
    task_id = str(uuid.uuid4())

    service = AsyncMock()
    service.process = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    service.run_task = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    service.emit_hook = AsyncMock(return_value=None)
    service.cancel_task = AsyncMock(return_value=False)

    queue = BuilderTaskQueue(service, log_path=log_path)
    queue.start()

    try:
        await queue.enqueue(_make_task(task_id))
        record = None
        for _ in range(100):
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion(task_id, timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None, "JSONL record must appear even on failure"
        assert record["task_id"] == task_id
        assert record["status"] == "failure"
        assert record["phase_completed"] == "failed"
    finally:
        await queue.stop()


# ---------------------------------------------------------------------------
# Integration: BuilderExecutor HTTP handler returns 202 (submitted) instantly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_returns_submitted_immediately(tmp_path: Path) -> None:
    """execute() must emit a ``submitted`` event and return without blocking.

    The test patches ``_task_queue`` with a real ``BuilderTaskQueue`` backed by
    a mock service so that we can verify:
    - HTTP handler returns quickly (< 2 s)
    - A JSONL record appears within 60 s (10 s in test with fast mock)
    """
    import time

    from app.agents.builder.agent import BuilderExecutor

    log_path = tmp_path / "tasks.jsonl"
    task_id = str(uuid.uuid4())
    ctx_id = str(uuid.uuid4())

    # Build a fake EventQueue that collects events
    events: list[Any] = []
    mock_event_queue = AsyncMock()
    mock_event_queue.enqueue_event = AsyncMock(side_effect=events.append)

    # Build a fake RequestContext
    mock_message = MagicMock()
    mock_message.parts = [MagicMock(root=MagicMock(text="Add fire-and-forget support"))]
    mock_message.metadata = {"a2a_task_id": task_id, "a2a_context_id": ctx_id}
    mock_context = MagicMock()
    mock_context.task_id = task_id
    mock_context.context_id = ctx_id
    mock_context.message = mock_message

    # Real queue with fast mock service
    service = _make_mock_service("Done. PR: https://github.com/org/repo/pull/7")
    real_queue = BuilderTaskQueue(service, log_path=log_path)
    real_queue.start()

    executor = BuilderExecutor()

    try:
        with patch("app.agents.builder.agent._task_queue", real_queue):
            t0 = time.monotonic()
            await executor.execute(mock_context, mock_event_queue)
            elapsed = time.monotonic() - t0

        # HTTP dispatch must be fast (task is queued, not executed inline)
        assert elapsed < 2.0, f"execute() took {elapsed:.2f}s — should return immediately"

        # The emitted event must be 'submitted' (202-semantics)
        assert len(events) == 1, "Exactly one event emitted for accepted task"
        event = events[0]
        assert event.status.state.value == "submitted", (
            f"Expected submitted, got {event.status.state}"
        )
        assert task_id in (event.status.message.parts[0].root.text if event.status.message else "")

        # Background task should complete and write the JSONL record
        record = None
        for _ in range(100):  # up to 10 s
            await asyncio.sleep(0.1)
            record = sync_wait_for_completion(task_id, timeout_seconds=0.0, log_path=log_path)
            if record is not None:
                break

        assert record is not None, "JSONL record must appear within 10 s"
        assert record["task_id"] == task_id
        assert record["status"] == "success"
        assert set(record.keys()) >= {
            "task_id",
            "status",
            "phase_completed",
            "commit_hash",
            "pr_url",
            "timestamp_utc",
        }, f"Missing keys in record: {record}"
    finally:
        await real_queue.stop()
