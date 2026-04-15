"""Background task queue and JSONL task logging for observe_agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from a2a.types import Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

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
    event_queue: Any


def _resolve_log_path(log_path: Path | None) -> Path:
    return log_path if log_path is not None else get_task_log_path()


def _task_message(text: str) -> Message:
    return Message(
        message_id=str(datetime.now(UTC).timestamp()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
    )


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
    """Concurrency-limited runner and task logger for observe requests."""

    def __init__(
        self,
        service: ObserveAgent,
        max_concurrent: int = 1,
        log_path: Path | None = None,
    ) -> None:
        self._service = service
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._log_path = _resolve_log_path(log_path)
        self._active_task_ids: set[str] = set()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info("ObserveTaskQueue: runner ready")

    async def stop(self, drain_timeout: float = 5.0) -> None:
        del drain_timeout
        if not self._started:
            return
        self._started = False
        logger.info("ObserveTaskQueue: runner stopped")

    async def enqueue(self, task: _QueuedTask) -> None:
        self._active_task_ids.add(task.task_id)
        logger.debug("ObserveTaskQueue: enqueued task_id=%s", task.task_id)
        started = False
        try:
            async with self._semaphore:
                started = True
                await self._run_task(task)
        except asyncio.CancelledError:
            if not started:
                append_task_record(
                    TaskRecord(
                        task_id=task.task_id,
                        context_id=task.context_id,
                        status="cancelled",
                        response_text=None,
                        error="Cancelled before execution.",
                        timestamp_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                    log_path=self._log_path,
                )
                logger.info("ObserveTaskQueue: cancelled before start task_id=%s", task.task_id)
            raise
        finally:
            self._active_task_ids.discard(task.task_id)

    async def cancel(self, task_id: str) -> bool:
        return task_id in self._active_task_ids

    async def _run_task(self, task: _QueuedTask) -> None:
        response_text: str | None = None
        error: str | None = None
        status = "failure"
        logger.info("ObserveTaskQueue: starting task_id=%s", task.task_id)
        try:
            await task.event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task.task_id,
                    context_id=task.context_id,
                    final=False,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=_task_message("Observe task is running."),
                        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                )
            )
            response_text = await self._service.process(task.request_text, task.request_ctx)
            status = "success"
        except asyncio.CancelledError:
            status = "cancelled"
            error = "Cancelled."
            logger.info("ObserveTaskQueue: task cancelled task_id=%s", task.task_id)
            raise
        except Exception as exc:
            error = str(exc)
            logger.exception("ObserveTaskQueue: task failed task_id=%s", task.task_id)
            await task.event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task.task_id,
                    context_id=task.context_id,
                    final=True,
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=_task_message(error or "Observe task failed."),
                        timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    ),
                )
            )
        finally:
            if status == "success":
                await task.event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task.task_id,
                        context_id=task.context_id,
                        final=True,
                        status=TaskStatus(
                            state=TaskState.completed,
                            message=_task_message(response_text or "Observe task completed."),
                            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        ),
                    )
                )
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
