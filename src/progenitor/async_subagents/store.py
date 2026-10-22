"""Tracked async-subagent persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.progenitor.async_subagents.models import AsyncSubagentTaskRecord
from src.progenitor.persistence.documents import StateDocuments


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


class AsyncSubagentStore:
    """Own async subagents document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        """Return all async subagent task records."""
        payload = await self._access.get("async_subagent_tasks")
        return [AsyncSubagentTaskRecord.model_validate(item) for item in payload]

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
        """Persist all async subagent task records."""
        await self._access.save(
            "async_subagent_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    async def get_async_subagent_task(self, task_id: str) -> AsyncSubagentTaskRecord | None:
        """Return one async subagent task by id, or ``None`` if not found."""
        return await get_async_subagent_task(
            task_id=task_id,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
        )

    async def upsert_async_subagent_task(self, task: AsyncSubagentTaskRecord) -> None:
        """Insert or update an async subagent task record."""
        await upsert_async_subagent_task(
            task=task,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )

    async def update_async_subagent_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to an async subagent task. Returns ``True`` if found."""
        return await update_async_subagent_task(
            task_id=task_id,
            updates=updates,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )
