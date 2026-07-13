"""Fire-and-forget queue for builder_agent task execution."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from a2a.types import Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

from .config import _default_project_root

if TYPE_CHECKING:
    from .runtime.background_build import BuildResult

logger = logging.getLogger(__name__)
DEFAULT_PROJECT_LOG_DIR = _default_project_root() / "logs"


def _require_agent_name() -> str:
    agent_name = os.environ.get("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set for builder_agent")
    return agent_name


def get_task_log_path() -> Path:
    agent_name = _require_agent_name()
    base_log_dir = Path(os.environ.get("PROGENITOR_LOG_DIR", str(DEFAULT_PROJECT_LOG_DIR)))
    return base_log_dir / agent_name / f"{agent_name}_tasks.jsonl"


TASK_LOG_PATH: Path | None = (
    get_task_log_path() if os.environ.get("AGENT_NAME", "").strip() else None
)

_append_lock = threading.Lock()


@dataclass
class TaskRecord:
    """Completion record written to the JSONL task log."""

    task_id: str
    status: str
    phase_completed: str
    build_id: str | None
    pr_url: str | None
    timestamp_utc: str
    error_message: str | None = None


def _resolve_log_path(log_path: Path | None) -> Path:
    return log_path if log_path is not None else get_task_log_path()


def _task_message(text: str) -> Message:
    return Message(
        message_id=str(datetime.now(UTC).timestamp()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
    )


def _status_event(
    task_id: str,
    context_id: str,
    state: TaskState,
    message_text: str,
    *,
    final: bool,
) -> TaskStatusUpdateEvent:
    """Build one `TaskStatusUpdateEvent` -- every push in `_run_task` differs
    only in these four values, so this replaces six near-identical inline
    constructions with one call each."""
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        final=final,
        status=TaskStatus(
            state=state,
            message=_task_message(message_text),
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )


def append_task_record(record: TaskRecord, log_path: Path | None = None) -> None:
    """Atomically append a task record to the JSONL log."""
    log_path = _resolve_log_path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(
            {
                "task_id": record.task_id,
                "status": record.status,
                "phase_completed": record.phase_completed,
                "build_id": record.build_id,
                "pr_url": record.pr_url,
                "timestamp_utc": record.timestamp_utc,
                "error_message": record.error_message,
            }
        )
        + "\n"
    )

    lock_path = log_path.with_suffix(".lock")
    with _append_lock:
        try:
            import fcntl

            with open(lock_path, "a", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    with open(log_path, "a", encoding="utf-8") as log_fh:
                        log_fh.write(line)
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            with open(log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write(line)
        except OSError as exc:
            logger.warning("Failed to append task record to %s: %s", log_path, exc)


def sync_wait_for_completion(
    task_id: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    log_path: Path | None = None,
) -> dict[str, Any] | None:
    """Poll the task log for a completion record."""
    import time

    log_path = _resolve_log_path(log_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = _find_record(task_id, log_path)
        if record is not None:
            return record
        time.sleep(poll_interval)
    return _find_record(task_id, log_path)


def _find_record(task_id: str, log_path: Path) -> dict[str, Any] | None:
    if not log_path.exists():
        return None
    try:
        with open(log_path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if record.get("task_id") == task_id:
                    return cast(dict[str, Any], record)
    except OSError:
        return None
    return None


class BuilderTaskQueue:
    """Runs builder requests to completion, awaited directly by the A2A
    executor's execute() call (see BuilderAgentExecutor's docstring for why:
    the framework closes the A2A event_queue the instant execute() returns,
    so a detached background task can never deliver events past that point).

    A semaphore still caps concurrency (matching BackgroundBuildRunner's own
    single-active-build contract) so a second concurrent request waits its
    turn -- via a plain `async with` on the semaphore inside the still-live
    execute() call -- rather than racing BackgroundBuildRunner's own "build
    already in progress" guard.
    """

    def __init__(
        self,
        handler: Callable[..., Awaitable[BuildResult]],
        max_concurrent: int = 1,
        log_path: Path | None = None,
    ) -> None:
        self._handler = handler
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._log_path = _resolve_log_path(log_path)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    async def run(
        self,
        task_id: str,
        context_id: str,
        request_text: str,
        request_ctx: dict[str, Any],
        event_queue: Any,
    ) -> None:
        """Run one build to completion, pushing status events as it goes.

        Must be awaited by the caller (BuilderAgentExecutor.execute), not
        fire-and-forgotten. Registers an internally-tracked asyncio.Task
        purely so `cancel()` has something to call .cancel() on -- it is
        still awaited here, so cancelling it correctly interrupts this call
        whether the task is waiting on the semaphore or actively running.
        """
        inner = asyncio.create_task(
            self._run_task(task_id, context_id, request_text, request_ctx, event_queue)
        )
        self._running_tasks[task_id] = inner
        try:
            await inner
        except asyncio.CancelledError:
            # cancel() can race in before `inner` ever gets a first turn on
            # the event loop -- asyncio then completes it as cancelled
            # without ever entering _run_task's body, so its own
            # try/except (and the terminal event it pushes on that path)
            # never runs. Push the same fallback here so callers still see
            # a terminal event and this coroutine still returns normally
            # instead of propagating the cancellation into
            # BuilderAgentExecutor.execute() (see its docstring).
            await event_queue.enqueue_event(
                _status_event(
                    task_id, context_id, TaskState.canceled, "Build task cancelled.", final=True
                )
            )
        finally:
            self._running_tasks.pop(task_id, None)

    def cancel(self, task_id: str) -> bool:
        task = self._running_tasks.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel any in-flight builds. Called on process shutdown."""
        running = list(self._running_tasks.values())
        for task in running:
            task.cancel()
        for task in running:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _run_task(
        self,
        task_id: str,
        context_id: str,
        request_text: str,
        request_ctx: dict[str, Any],
        event_queue: Any,
    ) -> None:
        record = TaskRecord(
            task_id=task_id,
            status="failure",
            phase_completed="accepted",
            build_id=None,
            pr_url=None,
            timestamp_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )

        # The semaphore acquisition itself lives inside this try (not the
        # other way around): a task cancelled while still queued behind it
        # (a second concurrent build waiting its turn, per max_concurrent=1)
        # raises CancelledError before the queued task's body ever runs. If
        # the `async with` wrapped this try instead, that cancellation would
        # propagate straight out of _run_task uncaught -- through run() and
        # into BuilderAgentExecutor.execute() -- which must not happen (see
        # that class's docstring): the framework only closes event_queue
        # once execute() returns normally, so an uncaught exception would
        # leave a live message/stream watcher hanging indefinitely instead
        # of seeing the stream end.
        try:
            async with self._semaphore:
                await event_queue.enqueue_event(
                    _status_event(
                        task_id,
                        context_id,
                        TaskState.working,
                        "Build task is running.",
                        final=False,
                    )
                )
                logger.info(
                    "BuilderTaskQueue: starting task task_id=%s text=%.80r",
                    task_id,
                    request_text,
                )
                result = await self._handler(
                    task_id,
                    context_id,
                    request_text,
                    request_ctx,
                    event_queue,
                )
                status = getattr(result.status, "value", str(result.status))
                record.status = "success" if status == "completed" else status
                record.phase_completed = "pr" if status == "completed" else status
                record.build_id = result.build_id
                record.pr_url = result.pr_url
                record.error_message = result.error_message
                if status == "completed":
                    summary = result.output_summary or "Build completed."
                    if result.pr_url:
                        summary = f"{summary}\nPR: {result.pr_url}"
                    await event_queue.enqueue_event(
                        _status_event(task_id, context_id, TaskState.completed, summary, final=True)
                    )
                elif status == "cancelled":
                    await event_queue.enqueue_event(
                        _status_event(
                            task_id,
                            context_id,
                            TaskState.canceled,
                            "Build task cancelled.",
                            final=True,
                        )
                    )
                else:
                    failure_text = (
                        result.error_message or result.output_summary or "Build task failed."
                    )
                    await event_queue.enqueue_event(
                        _status_event(
                            task_id, context_id, TaskState.failed, failure_text, final=True
                        )
                    )
                logger.info(
                    "BuilderTaskQueue: task completed task_id=%s status=%s pr_url=%s",
                    task_id,
                    record.status,
                    record.pr_url,
                )
        except asyncio.CancelledError:
            record.status = "cancelled"
            record.phase_completed = "cancelled"
            record.error_message = "Build task cancelled"
            await event_queue.enqueue_event(
                _status_event(
                    task_id, context_id, TaskState.canceled, "Build task cancelled.", final=True
                )
            )
            logger.info("BuilderTaskQueue: task cancelled task_id=%s", task_id)
            # Deliberately not re-raised: this coroutine runs inside its
            # own asyncio.Task (`inner` in run()), and completing
            # normally here (rather than propagating CancelledError back
            # through `await inner`) lets execute() finish normally too,
            # so the framework closes the A2A event_queue cleanly instead
            # of tearing down the request with an exception.
        except Exception as exc:
            record.status = "failure"
            record.phase_completed = "failed"
            record.error_message = str(exc)
            await event_queue.enqueue_event(
                _status_event(
                    task_id,
                    context_id,
                    TaskState.failed,
                    record.error_message or "Build task failed.",
                    final=True,
                )
            )
            logger.exception("BuilderTaskQueue: task failed task_id=%s", task_id)
        finally:
            record.timestamp_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            append_task_record(record, self._log_path)
