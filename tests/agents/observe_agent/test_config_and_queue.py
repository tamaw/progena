import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

from app.agents.observe_agent.queue_worker import (
    ObserveTaskQueue,
    TaskRecord,
    _QueuedTask,
    append_task_record,
    sync_wait_for_completion,
)


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
