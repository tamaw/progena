"""Background task queue and JSONL task logging for observe_agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .agent import ObserveAgent

logger = logging.getLogger(__name__)
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"


def _agent_name() -> str:
    return os.getenv("AGENT_NAME", "observe_agent").strip() or "observe_agent"


def get_task_log_path() -> Path:
    agent_name = _agent_name()
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
    context_id: str
    status: str
    response_text: str | None
    error: str | None
    timestamp_utc: str


@dataclass
class _QueuedTask:
    """Internal envelope used to pass work to the background worker."""

    task_id: str
    context_id: str
    request_text: str
    request_ctx: dict[str, Any]


def _resolve_log_path(log_path: Path | None) -> Path:
    return log_path if log_path is not None else get_task_log_path()


def append_task_record(record: TaskRecord, log_path: Path | None = None) -> None:
    """Atomically append *record* as a JSON line to *log_path*."""
    log_path = _resolve_log_path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(
            {
                "task_id": record.task_id,
                "context_id": record.context_id,
                "status": record.status,
                "response_text": record.response_text,
                "error": record.error,
                "timestamp_utc": record.timestamp_utc,
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
            logger.warning("Failed to write task record to %s: %s", log_path, exc)
            return


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
                    entry = json.loads(raw)
                    if entry.get("task_id") == task_id:
                        return cast(dict[str, Any], entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return None


def sync_wait_for_completion(
    task_id: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    log_path: Path | None = None,
) -> dict[str, Any] | None:
    """Wait until a completion record for *task_id* appears in *log_path*."""
    import time

    log_path = _resolve_log_path(log_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = _find_record(task_id, log_path)
        if record is not None:
            return record
        time.sleep(poll_interval)
    return _find_record(task_id, log_path)


class ObserveTaskQueue:
    """In-process asyncio queue for background observe requests."""

    def __init__(
        self,
        service: ObserveAgent,
        max_concurrent: int = 1,
        log_path: Path | None = None,
    ) -> None:
        self._service = service
        self._queue: asyncio.Queue[_QueuedTask] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._worker_task: asyncio.Task[None] | None = None
        self._log_path = _resolve_log_path(log_path)
        self._queued_task_ids: set[str] = set()
        self._cancelled_task_ids: set[str] = set()

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._dequeue_loop())
            logger.info("ObserveTaskQueue: worker started")

    async def stop(self, drain_timeout: float = 5.0) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=drain_timeout)
            self._worker_task = None
            logger.info("ObserveTaskQueue: worker stopped")

    async def enqueue(self, task: _QueuedTask) -> None:
        self._queued_task_ids.add(task.task_id)
        await self._queue.put(task)
        logger.debug("ObserveTaskQueue: enqueued task_id=%s", task.task_id)

    async def cancel(self, task_id: str) -> bool:
        if task_id in self._queued_task_ids:
            self._cancelled_task_ids.add(task_id)
            return True
        return False

    async def _dequeue_loop(self) -> None:
        while True:
            try:
                task = await self._queue.get()
                self._queued_task_ids.discard(task.task_id)
                if task.task_id in self._cancelled_task_ids:
                    self._cancelled_task_ids.discard(task.task_id)
                    self._queue.task_done()
                    logger.info("ObserveTaskQueue: skipped cancelled task_id=%s", task.task_id)
                    continue
                asyncio.ensure_future(self._run_task(task))
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("ObserveTaskQueue: dequeue loop cancelled")
                break
            except Exception:
                logger.exception("ObserveTaskQueue: unexpected dequeue loop error")

    async def _run_task(self, task: _QueuedTask) -> None:
        async with self._semaphore:
            response_text: str | None = None
            error: str | None = None
            status = "failure"
            logger.info("ObserveTaskQueue: starting task_id=%s", task.task_id)
            try:
                response_text = await self._service.process(task.request_text, task.request_ctx)
                status = "success"
            except Exception as exc:
                error = str(exc)
                logger.exception("ObserveTaskQueue: task failed task_id=%s", task.task_id)
            finally:
                append_task_record(
                    TaskRecord(
                        task_id=task.task_id,
                        context_id=task.context_id,
                        status=status,
                        response_text=response_text,
                        error=error,
                        timestamp_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                    log_path=self._log_path,
                )
                logger.info("ObserveTaskQueue: finished task_id=%s status=%s", task.task_id, status)
