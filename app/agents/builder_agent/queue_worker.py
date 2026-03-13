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

if TYPE_CHECKING:
    from .runtime.background_build import BuildResult

logger = logging.getLogger(__name__)

_agent_name = os.environ.get("AGENT_NAME", "builder_agent")
_base_log_dir = Path(os.environ.get("PROGENITOR_LOG_DIR", "/app/logs"))
TASK_LOG_PATH = Path(
    os.environ.get("BUILDER_TASK_LOG", str(_base_log_dir / _agent_name / f"{_agent_name}_tasks.jsonl"))
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


@dataclass
class _QueuedTask:
    """Internal task envelope."""

    task_id: str
    context_id: str
    request_text: str
    request_ctx: dict[str, Any]


def append_task_record(record: TaskRecord, log_path: Path = TASK_LOG_PATH) -> None:
    """Atomically append a task record to the JSONL log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "task_id": record.task_id,
            "status": record.status,
            "phase_completed": record.phase_completed,
            "build_id": record.build_id,
            "pr_url": record.pr_url,
            "timestamp_utc": record.timestamp_utc,
            "error_message": record.error_message,
        }
    ) + "\n"

    lock_path = log_path.with_suffix(".lock")
    with _append_lock:
        try:
            import fcntl

            with open(lock_path, "a", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
                try:
                    with open(log_path, "a", encoding="utf-8") as log_fh:
                        log_fh.write(line)
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        except ImportError:
            with open(log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write(line)
        except OSError as exc:
            logger.warning("Failed to append task record to %s: %s", log_path, exc)


def sync_wait_for_completion(
    task_id: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    log_path: Path = TASK_LOG_PATH,
) -> dict[str, Any] | None:
    """Poll the task log for a completion record."""
    import time

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
    """In-process queue that runs builder requests in background tasks."""

    def __init__(
        self,
        handler: Callable[[str, str, str, dict[str, Any]], Awaitable[BuildResult]],
        max_concurrent: int = 1,
        log_path: Path = TASK_LOG_PATH,
    ) -> None:
        self._handler = handler
        self._queue: asyncio.Queue[_QueuedTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._worker_task: asyncio.Task[None] | None = None
        self._log_path = log_path
        self._queued_task_ids: set[str] = set()
        self._cancelled_task_ids: set[str] = set()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._dequeue_loop())
            logger.info("BuilderTaskQueue: worker started")

    async def stop(self, drain_timeout: float = 5.0) -> None:
        running_tasks = list(self._running_tasks.values())
        for task in running_tasks:
            task.cancel()
        for task in running_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=drain_timeout)
            self._worker_task = None
            logger.info("BuilderTaskQueue: worker stopped")

    async def enqueue(self, task: _QueuedTask) -> None:
        self._queued_task_ids.add(task.task_id)
        await self._queue.put(task)
        logger.debug("BuilderTaskQueue: enqueued task_id=%s", task.task_id)

    async def cancel(self, task_id: str) -> bool:
        if task_id in self._queued_task_ids:
            self._cancelled_task_ids.add(task_id)
            return True

        running_task = self._running_tasks.get(task_id)
        if running_task is None or running_task.done():
            return False
        running_task.cancel()
        return True

    async def _dequeue_loop(self) -> None:
        while True:
            try:
                task = await self._queue.get()
                self._queued_task_ids.discard(task.task_id)
                if task.task_id in self._cancelled_task_ids:
                    self._cancelled_task_ids.discard(task.task_id)
                    self._queue.task_done()
                    logger.info("BuilderTaskQueue: skipped cancelled queued task_id=%s", task.task_id)
                    continue

                running_task = asyncio.create_task(self._run_task(task))
                self._running_tasks[task.task_id] = running_task
                running_task.add_done_callback(self._make_done_callback(task.task_id))
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("BuilderTaskQueue: dequeue loop cancelled")
                break
            except Exception:
                logger.exception("BuilderTaskQueue: unexpected dequeue error")

    def _make_done_callback(self, task_id: str) -> Callable[[asyncio.Task[None]], None]:
        def _done_callback(_task: asyncio.Task[None]) -> None:
            self._running_tasks.pop(task_id, None)

        return _done_callback

    async def _run_task(self, task: _QueuedTask) -> None:
        async with self._semaphore:
            record = TaskRecord(
                task_id=task.task_id,
                status="failure",
                phase_completed="accepted",
                build_id=None,
                pr_url=None,
                timestamp_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )

            try:
                result = await self._handler(
                    task.task_id,
                    task.context_id,
                    task.request_text,
                    task.request_ctx,
                )
                status = getattr(result.status, "value", str(result.status))
                record.status = "success" if status == "completed" else status
                record.phase_completed = "pr" if status == "completed" else status
                record.build_id = result.build_id
                record.pr_url = result.pr_url
                record.error_message = result.error_message
            except asyncio.CancelledError:
                record.status = "cancelled"
                record.phase_completed = "cancelled"
                record.error_message = "Build task cancelled"
                logger.info("BuilderTaskQueue: task cancelled task_id=%s", task.task_id)
            except Exception as exc:
                record.status = "failure"
                record.phase_completed = "failed"
                record.error_message = str(exc)
                logger.exception("BuilderTaskQueue: task failed task_id=%s", task.task_id)
            finally:
                record.timestamp_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                append_task_record(record, self._log_path)
