"""Tracked A2A task persistence helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.progenitor.a2a.models import A2ATaskRecord
from app.progenitor.persistence.documents import StateDocuments


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


class A2ATaskStore:
    """Own a2a document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return all A2A task records."""
        payload = await self._access.get("a2a_tasks")
        return [A2ATaskRecord.model_validate(item) for item in payload]

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
        """Persist all A2A task records."""
        await self._access.save(
            "a2a_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    async def get_active_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return A2A tasks that are still active (not completed/failed)."""
        return await get_active_a2a_tasks(get_a2a_tasks_fn=self.get_a2a_tasks)

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        """Return one A2A task by id, or ``None`` if not found."""
        return await get_a2a_task(task_id=task_id, get_a2a_tasks_fn=self.get_a2a_tasks)

    async def upsert_a2a_task(self, task: A2ATaskRecord) -> None:
        """Insert or update an A2A task record."""
        await upsert_a2a_task(
            task=task,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )

    async def update_a2a_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply partial updates to an A2A task. Returns ``True`` if the task existed."""
        return await update_a2a_task(
            task_id=task_id,
            updates=updates,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )
