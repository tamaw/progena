"""Tracked async-subagent persistence helpers."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from .models import AsyncSubagentTaskRecord


async def get_async_subagent_tasks(
    *,
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    async_subagent_tasks_path: Path,
) -> list[AsyncSubagentTaskRecord]:
    if "async_subagent_tasks" in state_cache:
        data = copy.deepcopy(cast(list[dict[str, Any]], state_cache["async_subagent_tasks"]))
        return [AsyncSubagentTaskRecord(**item) for item in data]
    async with file_lock("async_subagent_tasks"):
        data = await read_json(async_subagent_tasks_path) or []
        task_rows = [item for item in data if isinstance(item, dict)]
        state_cache["async_subagent_tasks"] = task_rows
        return [AsyncSubagentTaskRecord(**item) for item in copy.deepcopy(task_rows)]


async def save_async_subagent_tasks(
    *,
    tasks: list[AsyncSubagentTaskRecord],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    async_subagent_tasks_path: Path,
) -> None:
    task_rows = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
    async with file_lock("async_subagent_tasks"):
        await atomic_write_json(async_subagent_tasks_path, task_rows)
        state_cache["async_subagent_tasks"] = task_rows


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
