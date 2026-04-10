"""Fire-and-forget task queue and worker for builder_progenitor.

Decouples HTTP dispatch from task execution so that long-running builds
(>30 s) never block the HTTP response.  On completion (success or failure)
a single JSON line is atomically appended to TASK_LOG_PATH so callers can
poll for results without holding an open connection.

Atomic append strategy
----------------------
* An ``fcntl.LOCK_EX`` advisory lock on ``<log>.lock`` serialises concurrent
  writers at the OS level (cross-process safe on Linux / POSIX).
* The actual ``open(path, "a")`` then ``write()`` call uses ``O_APPEND``
  which is atomic for writes smaller than ``PIPE_BUF`` (4 KiB) on POSIX.
* If ``fcntl`` is unavailable (e.g. Windows CI) we fall back to a
  ``threading.Lock`` for in-process safety only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .agent import BuilderService

from .hooks import HookContext

logger = logging.getLogger(__name__)
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _require_agent_name() -> str:
    agent_name = os.environ.get("AGENT_NAME", "").strip()
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set for builder_progenitor")
    return agent_name


def get_task_log_path() -> Path:
    agent_name = _require_agent_name()
    base_log_dir = Path(os.environ.get("PROGENITOR_LOG_DIR", str(DEFAULT_PROJECT_LOG_DIR)))
    return base_log_dir / agent_name / f"{agent_name}_tasks.jsonl"


TASK_LOG_PATH: Path | None = (
    get_task_log_path() if os.environ.get("AGENT_NAME", "").strip() else None
)

_PR_URL_RE = re.compile(r"https://github\.com/\S+/pull/\d+")
_COMMIT_HASH_RE = re.compile(r"(?:SHA|sha|commit|hash)[:\s]+([0-9a-f]{7,40})\b", re.IGNORECASE)

# In-process lock for multi-threaded safety
_append_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TaskRecord:
    """Completion record written to the JSONL task log."""

    task_id: str
    status: str  # "success" | "failure"
    phase_completed: str
    commit_hash: str | None
    pr_url: str | None
    timestamp_utc: str  # ISO-8601 with Z suffix


@dataclass
class _QueuedTask:
    """Internal envelope used to pass work to the background worker."""

    task_id: str
    context_id: str
    request_text: str
    request_ctx: dict[str, Any]


# ---------------------------------------------------------------------------
# Atomic JSONL append
# ---------------------------------------------------------------------------


def _resolve_log_path(log_path: Path | None) -> Path:
    return log_path if log_path is not None else get_task_log_path()


def append_task_record(record: TaskRecord, log_path: Path | None = None) -> None:
    """Atomically append *record* as a JSON line to *log_path*.

    The parent directory is created when missing.  A ``<log>.lock`` file is
    used as an exclusive advisory lock so that concurrent processes do not
    interleave partial writes.  An in-process ``threading.Lock`` adds an
    extra safety layer for in-process concurrency.
    """
    log_path = _resolve_log_path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    line = (
        json.dumps(
            {
                "task_id": record.task_id,
                "status": record.status,
                "phase_completed": record.phase_completed,
                "commit_hash": record.commit_hash,
                "pr_url": record.pr_url,
                "timestamp_utc": record.timestamp_utc,
            }
        )
        + "\n"
    )

    lock_path = log_path.with_suffix(".lock")

    with _append_lock:
        try:
            import fcntl  # POSIX only

            with open(lock_path, "a", encoding="utf-8") as lock_fh:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    with open(log_path, "a", encoding="utf-8") as log_fh:
                        log_fh.write(line)
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except ImportError:
            # Non-POSIX fallback: threading.Lock already held above
            with open(log_path, "a", encoding="utf-8") as log_fh:
                log_fh.write(line)
        except OSError as exc:
            logger.warning("Failed to write task record to %s: %s", log_path, exc)
            return

    logger.info(
        "Task record appended: task_id=%s status=%s phase=%s",
        record.task_id,
        record.status,
        record.phase_completed,
    )


# ---------------------------------------------------------------------------
# Synchronous polling helper
# ---------------------------------------------------------------------------


def sync_wait_for_completion(
    task_id: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
    log_path: Path | None = None,
) -> dict[str, Any] | None:
    """Block until a completion record for *task_id* appears in *log_path*.

    Reads the JSONL file on each poll cycle and returns the matching record
    dict on success, or ``None`` when *timeout_seconds* expires.

    This helper is intended for backward-compatible callers that cannot use
    async I/O, and for integration tests that need to verify task completion.
    """
    import time

    log_path = _resolve_log_path(log_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = _find_record(task_id, log_path)
        if record is not None:
            return record
        time.sleep(poll_interval)
    return _find_record(task_id, log_path)  # one final check at deadline


def _find_record(task_id: str, log_path: Path) -> dict[str, Any] | None:
    """Return the first JSONL record matching *task_id*, or ``None``."""
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


# ---------------------------------------------------------------------------
# Background queue / worker
# ---------------------------------------------------------------------------


class BuilderTaskQueue:
    """In-process asyncio queue with a persistent background worker.

    Tasks are dequeued one-by-one and executed concurrently up to
    ``max_concurrent`` (default 1).  Results are logged to TASK_LOG_PATH.
    """

    def __init__(
        self,
        service: BuilderService,
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background dequeue loop (must be called inside an event loop)."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._dequeue_loop())
            logger.info("BuilderTaskQueue: worker started")

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Cancel the dequeue loop and wait for it to exit."""
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=drain_timeout)
            self._worker_task = None
            logger.info("BuilderTaskQueue: worker stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, task: _QueuedTask) -> None:
        """Enqueue a task for background execution."""
        self._queued_task_ids.add(task.task_id)
        await self._queue.put(task)
        logger.debug("BuilderTaskQueue: enqueued task_id=%s", task.task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a queued or active task by ID."""
        if task_id in self._queued_task_ids:
            self._cancelled_task_ids.add(task_id)
            return True
        return await self._service.cancel_task(task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _dequeue_loop(self) -> None:
        """Continuously dequeue tasks and launch them as background coroutines."""
        while True:
            try:
                task = await self._queue.get()
                self._queued_task_ids.discard(task.task_id)
                if task.task_id in self._cancelled_task_ids:
                    self._cancelled_task_ids.discard(task.task_id)
                    self._queue.task_done()
                    logger.info(
                        "BuilderTaskQueue: skipped cancelled queued task_id=%s", task.task_id
                    )
                    continue
                asyncio.ensure_future(self._run_task(task))
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("BuilderTaskQueue: dequeue loop cancelled")
                break
            except Exception:
                logger.exception("BuilderTaskQueue: unexpected error in dequeue loop")

    async def _run_task(self, task: _QueuedTask) -> None:
        """Execute a single task under the concurrency semaphore and log the result."""
        async with self._semaphore:
            status: str = "failure"
            phase_completed: str = "accepted"
            commit_hash: str | None = None
            pr_url: str | None = None
            planned_build = None

            logger.info(
                "BuilderTaskQueue: starting task task_id=%s text=%.80r",
                task.task_id,
                task.request_text,
            )

            try:
                planned_build = await self._service.plan_task(task.request_text, task.request_ctx)
                logger.info(
                    "BuilderTaskQueue: planned task task_id=%s work_item_id=%s branch=%s task_name=%s",
                    task.task_id,
                    planned_build.plan.work_item_id,
                    planned_build.plan.branch_name,
                    planned_build.plan.task_name,
                )
                await self._service.emit_hook(
                    "build_started",
                    HookContext(
                        event="build_started",
                        task_id=task.task_id,
                        request_text=task.request_text,
                        request_context=task.request_ctx,
                        planned_build=planned_build,
                        metadata={
                            "branch_name": planned_build.plan.branch_name,
                            "work_item_id": planned_build.plan.work_item_id,
                            "task_name": planned_build.plan.task_name,
                            "planned_at_utc": planned_build.planned_at_utc,
                        },
                    ),
                )
                response = await self._service.run_task(
                    task.task_id,
                    task.request_text,
                    task.request_ctx,
                    planned_build,
                )
                status = "success"
                phase_completed = "pr"

                pr_match = _PR_URL_RE.search(response)
                if pr_match:
                    pr_url = pr_match.group(0)

                commit_match = _COMMIT_HASH_RE.search(response)
                if commit_match:
                    commit_hash = commit_match.group(1)

                logger.info(
                    "BuilderTaskQueue: task completed task_id=%s pr_url=%s",
                    task.task_id,
                    pr_url,
                )
                await self._service.emit_hook(
                    "build_completed",
                    HookContext(
                        event="build_completed",
                        task_id=task.task_id,
                        request_text=task.request_text,
                        request_context=task.request_ctx,
                        planned_build=planned_build,
                        result_text=response,
                        metadata={
                            "pr_url": pr_url,
                            "commit_hash": commit_hash,
                            "phase_completed": phase_completed,
                        },
                    ),
                )

            except asyncio.CancelledError:
                status = "cancelled"
                phase_completed = "cancelled"
                logger.info("BuilderTaskQueue: task cancelled task_id=%s", task.task_id)
                await self._service.emit_hook(
                    "build_cancelled",
                    HookContext(
                        event="build_cancelled",
                        task_id=task.task_id,
                        request_text=task.request_text,
                        request_context=task.request_ctx,
                        planned_build=planned_build,
                        error="Build was cancelled.",
                    ),
                )
            except Exception as exc:
                status = "failure"
                phase_completed = "failed"
                logger.exception(
                    "BuilderTaskQueue: task failed task_id=%s error=%s",
                    task.task_id,
                    exc,
                )
                await self._service.emit_hook(
                    "build_failed",
                    HookContext(
                        event="build_failed",
                        task_id=task.task_id,
                        request_text=task.request_text,
                        request_context=task.request_ctx,
                        planned_build=planned_build,
                        metadata={"error_type": type(exc).__name__},
                        error=str(exc),
                    ),
                )

            finally:
                record = TaskRecord(
                    task_id=task.task_id,
                    status=status,
                    phase_completed=phase_completed,
                    commit_hash=commit_hash,
                    pr_url=pr_url,
                    timestamp_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                )
                try:
                    append_task_record(record, self._log_path)
                except Exception:
                    logger.exception(
                        "BuilderTaskQueue: failed to append task record task_id=%s",
                        task.task_id,
                    )
