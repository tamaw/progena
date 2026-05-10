"""Tracked A2A task persistence helpers."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from .models import A2ATaskRecord


async def get_a2a_tasks(
    *,
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    a2a_tasks_path: Path,
) -> list[A2ATaskRecord]:
    if "a2a_tasks" in state_cache:
        data = copy.deepcopy(cast(list[dict[str, Any]], state_cache["a2a_tasks"]))
        return [A2ATaskRecord(**item) for item in data]
    async with file_lock("a2a_tasks"):
        data = await read_json(a2a_tasks_path) or []
        task_rows = [item for item in data if isinstance(item, dict)]
        state_cache["a2a_tasks"] = task_rows
        return [A2ATaskRecord(**item) for item in copy.deepcopy(task_rows)]


async def save_a2a_tasks(
    *,
    tasks: list[A2ATaskRecord],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    a2a_tasks_path: Path,
) -> None:
    task_rows = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
    async with file_lock("a2a_tasks"):
        await atomic_write_json(a2a_tasks_path, task_rows)
        state_cache["a2a_tasks"] = task_rows


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
