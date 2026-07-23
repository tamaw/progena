"""Tracked A2A task persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .models import A2ATaskRecord


async def get_active_a2a_tasks(
    *,
    get_a2a_tasks_fn: Callable[[], Awaitable[list[A2ATaskRecord]]],
) -> list[A2ATaskRecord]:
    """Return tasks that still need polling or a completion notification.

    Historical terminal tasks whose notifications are already sent (or never
    wanted) are excluded so pollers don't rescan the full task history forever.
    """
    return [
        task
        for task in await get_a2a_tasks_fn()
        if not task.is_terminal or (task.notify_on_completion and task.notification_sent_at is None)
    ]


async def get_a2a_task(
    *,
    task_id: str,
    get_a2a_tasks_fn: Callable[[], Awaitable[list[A2ATaskRecord]]],
) -> A2ATaskRecord | None:
    for task in await get_a2a_tasks_fn():
        if task.task_id == task_id:
            return task
    return None


async def upsert_a2a_task(
    *,
    task: A2ATaskRecord,
    get_a2a_tasks_fn: Callable[[], Awaitable[list[A2ATaskRecord]]],
    save_a2a_tasks_fn: Callable[[list[A2ATaskRecord]], Awaitable[None]],
) -> None:
    tasks = await get_a2a_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id == task.task_id:
            tasks[index] = task
            break
    else:
        tasks.append(task)
    await save_a2a_tasks_fn(tasks)


async def update_a2a_task(
    *,
    task_id: str,
    updates: dict[str, Any],
    get_a2a_tasks_fn: Callable[[], Awaitable[list[A2ATaskRecord]]],
    save_a2a_tasks_fn: Callable[[list[A2ATaskRecord]], Awaitable[None]],
) -> bool:
    tasks = await get_a2a_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id != task_id:
            continue
        payload = existing.model_dump()
        payload.update(updates)
        tasks[index] = A2ATaskRecord(**payload)
        await save_a2a_tasks_fn(tasks)
        return True
    return False
