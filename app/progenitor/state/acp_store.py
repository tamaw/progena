"""Tracked ACP task persistence helpers (file-backed fallback).

Mirror of ``a2a_store.py`` for ``ACPTaskRecord``. The state manager prefers the
native runtime document store; these helpers back the file-based path.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from .models import ACPTaskRecord


async def get_acp_tasks(
    *,
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    acp_tasks_path: Path,
) -> list[ACPTaskRecord]:
    if "acp_tasks" in state_cache:
        data = copy.deepcopy(cast(list[dict[str, Any]], state_cache["acp_tasks"]))
        return [ACPTaskRecord(**item) for item in data]
    async with file_lock("acp_tasks"):
        data = await read_json(acp_tasks_path) or []
        task_rows = [item for item in data if isinstance(item, dict)]
        state_cache["acp_tasks"] = task_rows
        return [ACPTaskRecord(**item) for item in copy.deepcopy(task_rows)]


async def save_acp_tasks(
    *,
    tasks: list[ACPTaskRecord],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    acp_tasks_path: Path,
) -> None:
    task_rows = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
    async with file_lock("acp_tasks"):
        await atomic_write_json(acp_tasks_path, task_rows)
        state_cache["acp_tasks"] = task_rows


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
