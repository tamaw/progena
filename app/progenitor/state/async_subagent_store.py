"""Tracked async-subagent persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .models import AsyncSubagentTaskRecord


async def get_async_subagent_task(
    *,
    task_id: str,
    get_async_subagent_tasks_fn: Callable[[], Awaitable[list[AsyncSubagentTaskRecord]]],
) -> AsyncSubagentTaskRecord | None:
    for task in await get_async_subagent_tasks_fn():
        if task.task_id == task_id:
            return task
    return None


async def upsert_async_subagent_task(
    *,
    task: AsyncSubagentTaskRecord,
    get_async_subagent_tasks_fn: Callable[[], Awaitable[list[AsyncSubagentTaskRecord]]],
    save_async_subagent_tasks_fn: Callable[[list[AsyncSubagentTaskRecord]], Awaitable[None]],
) -> None:
    tasks = await get_async_subagent_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id == task.task_id:
            tasks[index] = task
            break
    else:
        tasks.append(task)
    await save_async_subagent_tasks_fn(tasks)


async def update_async_subagent_task(
    *,
    task_id: str,
    updates: dict[str, Any],
    get_async_subagent_tasks_fn: Callable[[], Awaitable[list[AsyncSubagentTaskRecord]]],
    save_async_subagent_tasks_fn: Callable[[list[AsyncSubagentTaskRecord]], Awaitable[None]],
) -> bool:
    tasks = await get_async_subagent_tasks_fn()
    for index, existing in enumerate(tasks):
        if existing.task_id != task_id:
            continue
        payload = existing.model_dump()
        payload.update(updates)
        tasks[index] = AsyncSubagentTaskRecord(**payload)
        await save_async_subagent_tasks_fn(tasks)
        return True
    return False
