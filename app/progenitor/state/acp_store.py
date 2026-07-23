"""Tracked ACP task persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .models import ACPTaskRecord


async def get_acp_task(
    *,
    task_id: str,
    get_acp_tasks_fn: Callable[[], Awaitable[list[ACPTaskRecord]]],
) -> ACPTaskRecord | None:
    for task in await get_acp_tasks_fn():
        if task.task_id == task_id:
            return task
    return None


async def upsert_acp_task(
    *,
    task: ACPTaskRecord,
    get_acp_tasks_fn: Callable[[], Awaitable[list[ACPTaskRecord]]],
    save_acp_tasks_fn: Callable[[list[ACPTaskRecord]], Awaitable[None]],
) -> None:
    tasks = await get_acp_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id == task.task_id:
            tasks[index] = task
            break
    else:
        tasks.append(task)
    await save_acp_tasks_fn(tasks)


async def update_acp_task(
    *,
    task_id: str,
    updates: dict[str, Any],
    get_acp_tasks_fn: Callable[[], Awaitable[list[ACPTaskRecord]]],
    save_acp_tasks_fn: Callable[[list[ACPTaskRecord]], Awaitable[None]],
) -> bool:
    tasks = await get_acp_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id != task_id:
            continue
        payload = existing.model_dump()
        payload.update(updates)
        tasks[index] = ACPTaskRecord(**payload)
        await save_acp_tasks_fn(tasks)
        return True
    return False
