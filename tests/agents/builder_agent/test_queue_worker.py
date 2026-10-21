from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from a2a.types import TaskState

from app.agents.builder_agent.queue_worker import BuilderTaskQueue, sync_wait_for_completion
from app.agents.builder_agent.runtime.background_build import BuildResult, BuildStatus
from app.agents.builder_agent.runtime.time_utils import utcnow


@pytest.mark.asyncio
async def test_run_executes_and_logs_completion(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()

    async def handler(
        task_id: str,
        context_id: str,
        request_text: str,
        request_ctx: dict[str, str],
        event_queue_arg: object,
    ) -> BuildResult:
        assert task_id == "task-2"
        assert context_id == "ctx-2"
        assert request_text == "Build something"
        assert request_ctx["a2a_task_id"] == "task-2"
        assert event_queue_arg is event_queue
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

    await queue.run("task-2", "ctx-2", "Build something", {"a2a_task_id": "task-2"}, event_queue)

    record = sync_wait_for_completion("task-2", timeout_seconds=0.0, log_path=log_path)
    assert record is not None
    assert record["status"] == "success"
    assert record["phase_completed"] == "pr"
    assert record["build_id"] == "build-2"
    assert record["pr_url"] == "https://github.com/org/repo/pull/2"
    states = [call.args[0].status.state for call in event_queue.enqueue_event.call_args_list]
    assert states == [TaskState.working, TaskState.completed]
    # run() must not return before the build finishes -- the A2A framework
    # closes event_queue the instant the caller's execute() returns, so
    # every push above has to happen before run() itself returns.
    assert "task-2" not in queue._running_tasks


@pytest.mark.asyncio
async def test_cancel_before_run_reports_nothing_to_cancel() -> None:
    handler = AsyncMock()
    queue = BuilderTaskQueue(handler)

    assert queue.cancel("never-started") is False
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_running_task_cancels_active_coroutine(tmp_path: Path) -> None:
    log_path = tmp_path / "tasks.jsonl"
    started = asyncio.Event()
    released = asyncio.Event()
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()

    async def handler(
        task_id: str,
        context_id: str,
        request_text: str,
        request_ctx: dict[str, str],
        event_queue_arg: object,
    ) -> BuildResult:
        del task_id, context_id, request_text, request_ctx, event_queue_arg
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
    run_task = asyncio.create_task(
        queue.run("running-task", "ctx-running", "Build now", {}, event_queue)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)

        cancelled = queue.cancel("running-task")
        assert cancelled is True

        # run() must complete normally (not raise) once _run_task swallows
        # its own CancelledError -- otherwise execute() would propagate an
        # exception out through the framework instead of returning cleanly.
        await asyncio.wait_for(run_task, timeout=2.0)

        record = sync_wait_for_completion("running-task", timeout_seconds=0.0, log_path=log_path)
        assert record is not None
        assert record["status"] == "cancelled"
        assert record["phase_completed"] == "cancelled"
        states = [call.args[0].status.state for call in event_queue.enqueue_event.call_args_list]
        assert states[-1] == TaskState.canceled
    finally:
        released.set()
        if not run_task.done():
            await run_task


@pytest.mark.asyncio
async def test_cancel_and_outer_task_cancellation_emit_single_terminal_outcome(
    tmp_path: Path,
) -> None:
    """If both cancellation paths fire, the client must still see exactly one
    terminal cancelled outcome and one persisted log record."""
    log_path = tmp_path / "tasks.jsonl"
    started = asyncio.Event()
    release = asyncio.Event()
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()

    async def handler(
        task_id: str,
        context_id: str,
        request_text: str,
        request_ctx: dict[str, str],
        event_queue_arg: object,
    ) -> BuildResult:
        del task_id, context_id, request_text, request_ctx, event_queue_arg
        started.set()
        await release.wait()
        return BuildResult(
            build_id="build-double-cancel",
            status=BuildStatus.COMPLETED,
            task_description="build_agent: double-cancel",
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    queue = BuilderTaskQueue(handler, log_path=log_path)
    run_task = asyncio.create_task(
        queue.run("double-cancel", "ctx-double-cancel", "Build now", {}, event_queue)
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2.0)
        assert queue.cancel("double-cancel") is True
        run_task.cancel()
        await asyncio.wait_for(run_task, timeout=2.0)
    finally:
        release.set()
        if not run_task.done():
            await run_task

    terminal_states = [
        call.args[0].status.state
        for call in event_queue.enqueue_event.call_args_list
        if call.args[0].final
    ]
    assert terminal_states == [TaskState.canceled]

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = sync_wait_for_completion("double-cancel", timeout_seconds=0.0, log_path=log_path)
    assert record is not None
    assert record["status"] == "cancelled"
    assert record["phase_completed"] == "cancelled"


@pytest.mark.asyncio
async def test_second_concurrent_run_waits_for_the_semaphore(tmp_path: Path) -> None:
    """max_concurrent=1 (the default, matching BackgroundBuildRunner's own
    single-active-build contract) must make a second concurrent run() wait
    its turn rather than running in parallel or failing outright."""
    log_path = tmp_path / "tasks.jsonl"
    release_first = asyncio.Event()
    order: list[str] = []

    async def handler(task_id, context_id, request_text, request_ctx, event_queue_arg):
        del context_id, request_text, request_ctx, event_queue_arg
        order.append(f"start:{task_id}")
        if task_id == "first":
            await release_first.wait()
        order.append(f"end:{task_id}")
        return BuildResult(
            build_id=f"build-{task_id}",
            status=BuildStatus.COMPLETED,
            task_description="build",
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    queue = BuilderTaskQueue(handler, log_path=log_path)
    event_queue_1 = AsyncMock()
    event_queue_1.enqueue_event = AsyncMock()
    event_queue_2 = AsyncMock()
    event_queue_2.enqueue_event = AsyncMock()

    first = asyncio.create_task(queue.run("first", "ctx", "req", {}, event_queue_1))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(queue.run("second", "ctx", "req", {}, event_queue_2))
    await asyncio.sleep(0.05)

    # Second must be registered (cancellable) even while still waiting on
    # the semaphore, but must not have started its handler yet.
    assert "second" in queue._running_tasks
    assert order == ["start:first"]

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2.0)

    assert order == ["start:first", "end:first", "start:second", "end:second"]


@pytest.mark.asyncio
async def test_cancel_while_queued_behind_semaphore_returns_cleanly(tmp_path: Path) -> None:
    """Regression: a task cancelled while still waiting on the semaphore
    (never got past `async with self._semaphore:`) used to propagate
    CancelledError straight out of run() uncaught -- the try/except lived
    inside the semaphore's `async with`, not wrapping it, so a cancellation
    during acquisition was never caught there. That would have propagated
    out of BuilderAgentExecutor.execute() too, which must always return
    normally (the framework only closes event_queue once execute() does).
    """
    log_path = tmp_path / "tasks.jsonl"
    release_first = asyncio.Event()
    order: list[str] = []

    async def handler(task_id, context_id, request_text, request_ctx, event_queue_arg):
        del context_id, request_text, request_ctx, event_queue_arg
        order.append(f"start:{task_id}")
        if task_id == "first":
            await release_first.wait()
        order.append(f"end:{task_id}")
        return BuildResult(
            build_id=f"build-{task_id}",
            status=BuildStatus.COMPLETED,
            task_description="build",
            started_at=utcnow(),
            completed_at=utcnow(),
        )

    queue = BuilderTaskQueue(handler, log_path=log_path)
    event_queue_1 = AsyncMock()
    event_queue_1.enqueue_event = AsyncMock()
    event_queue_2 = AsyncMock()
    event_queue_2.enqueue_event = AsyncMock()

    first = asyncio.create_task(queue.run("first", "ctx", "req", {}, event_queue_1))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(queue.run("second", "ctx", "req", {}, event_queue_2))
    await asyncio.sleep(0.05)
    assert "second" in queue._running_tasks
    assert order == ["start:first"]

    assert queue.cancel("second") is True

    # Must complete normally -- not raise -- even though "second" was
    # cancelled before its handler ever ran (still queued behind the
    # semaphore the whole time).
    await asyncio.wait_for(second, timeout=2.0)

    states = [call.args[0].status.state for call in event_queue_2.enqueue_event.call_args_list]
    assert states == [TaskState.canceled]
    assert "second" not in queue._running_tasks
    assert order == ["start:first"]

    release_first.set()
    await asyncio.wait_for(first, timeout=2.0)


@pytest.mark.asyncio
async def test_cancel_before_inner_task_ever_starts_still_returns_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: if `inner` (the asyncio.Task run() creates) is cancelled
    before the event loop ever gives it a first turn, asyncio completes it
    as cancelled without executing _run_task's body at all -- so _run_task's
    own try/except CancelledError (and the terminal event it pushes on that
    path) never runs. run() must catch that itself and push the same
    fallback event, so it still returns normally instead of propagating the
    cancellation into BuilderAgentExecutor.execute().

    Reproduced deterministically by cancelling `inner` the instant it is
    created, before run() reaches `await inner` -- matching real asyncio
    semantics where a task cancelled before its first step never runs its
    coroutine body.
    """
    event_queue = AsyncMock()
    event_queue.enqueue_event = AsyncMock()
    handler = AsyncMock()  # must never be called: cancelled before it can start

    queue = BuilderTaskQueue(handler)

    real_create_task = asyncio.create_task

    def create_task_and_cancel_immediately(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        task.cancel()
        return task

    monkeypatch.setattr(asyncio, "create_task", create_task_and_cancel_immediately)

    await queue.run("raced-task", "ctx-raced", "req", {}, event_queue)

    handler.assert_not_called()
    states = [call.args[0].status.state for call in event_queue.enqueue_event.call_args_list]
    assert states == [TaskState.canceled]
    assert "raced-task" not in queue._running_tasks
