"""Persistence helpers for queued background work items."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from .models import BackgroundTaskEntry


async def get_background_tasks(
    *,
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    background_tasks_path: Path,
) -> list[BackgroundTaskEntry]:
    if "background_tasks" in state_cache:
        data = copy.deepcopy(cast(list[dict[str, Any]], state_cache["background_tasks"]))
        return [BackgroundTaskEntry(**item) for item in data]
    async with file_lock("background_tasks"):
        data = await read_json(background_tasks_path) or []
        rows = [item for item in data if isinstance(item, dict)]
        state_cache["background_tasks"] = rows
        return [BackgroundTaskEntry(**item) for item in copy.deepcopy(rows)]


async def save_background_tasks(
    *,
    tasks: list[BackgroundTaskEntry],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    background_tasks_path: Path,
) -> None:
    rows = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
    async with file_lock("background_tasks"):
        await atomic_write_json(background_tasks_path, rows)
        state_cache["background_tasks"] = rows


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
