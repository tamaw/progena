"""Persistence helpers for queued background work items."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.progena.orchestration.state import BackgroundTaskEntry

BACKGROUND_TASKS_DOCUMENT_KEY = "background_tasks"
BACKGROUND_TASKS_ADVISORY_LOCK_KEY = BACKGROUND_TASKS_DOCUMENT_KEY
ROUTINE_RUN_TASK_KIND = "routine_run"


async def enqueue_background_task(
    *,
    task: BackgroundTaskEntry,
    get_background_tasks_fn: Callable[[], Awaitable[list[BackgroundTaskEntry]]],
    save_background_tasks_fn: Callable[[list[BackgroundTaskEntry]], Awaitable[None]],
) -> None:
    tasks = await get_background_tasks_fn()
    tasks.append(task)
    await save_background_tasks_fn(tasks)


async def dequeue_background_task(
    *,
    get_background_tasks_fn: Callable[[], Awaitable[list[BackgroundTaskEntry]]],
    save_background_tasks_fn: Callable[[list[BackgroundTaskEntry]], Awaitable[None]],
) -> BackgroundTaskEntry | None:
    tasks = await get_background_tasks_fn()
    if not tasks:
        return None
    task = tasks.pop(0)
    await save_background_tasks_fn(tasks)
    return task
