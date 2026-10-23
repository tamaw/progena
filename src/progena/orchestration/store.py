"""Background document persistence."""

from __future__ import annotations

from contextlib import asynccontextmanager

import src.progena.orchestration.background as background_store
from src.progena.orchestration.state import BackgroundTaskEntry
from src.progena.persistence.documents import StateDocuments


class BackgroundStore:
    """Own background document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_background_tasks(self) -> list[BackgroundTaskEntry]:
        """Return all background task entries."""
        payload = await self._access.get("background_tasks")
        return [BackgroundTaskEntry.model_validate(item) for item in payload]

    async def save_background_tasks(self, tasks: list[BackgroundTaskEntry]) -> None:
        """Persist all background task entries."""
        await self._access.save(
            "background_tasks",
            [task.model_dump(mode="json") for task in tasks],
        )

    @asynccontextmanager
    async def _with_background_tasks_lock(self):
        """Acquire a Postgres advisory lock for atomic background_tasks RMW.

        Yields immediately when no database_url is configured (in-memory store).
        """
        if not self._access.database_url:
            yield
            return
        from psycopg import AsyncConnection

        async with await AsyncConnection.connect(
            self._access.database_url, autocommit=True
        ) as conn:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended('background_tasks', 0))")
            try:
                yield
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended('background_tasks', 0))"
                )

    async def enqueue_background_task(self, task: BackgroundTaskEntry) -> None:
        """Add a background task to the queue."""
        async with self._with_background_tasks_lock():
            await background_store.enqueue_background_task(
                task=task,
                get_background_tasks_fn=self.get_background_tasks,
                save_background_tasks_fn=self.save_background_tasks,
            )

    async def dequeue_background_task(self) -> BackgroundTaskEntry | None:
        """Remove and return the next background task, or ``None`` if empty."""
        async with self._with_background_tasks_lock():
            return await background_store.dequeue_background_task(
                get_background_tasks_fn=self.get_background_tasks,
                save_background_tasks_fn=self.save_background_tasks,
            )
