"""Coalescing, single-flight scheduler for plugin reconciler runs.

Store-change notifications (agent edits to ``/config/plugins.json``, UI-driven
installs) can arrive in bursts.  Running a full reconcile per notification
would waste work and risk interleaving destructive skill-document edits.

This scheduler guarantees:
- **Single-flight**: at most one reconcile runs at a time.
- **Coalescing**: a burst of requests collapses to a single extra run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class PluginSyncScheduler:
    """Serialize and coalesce plugin reconcile work onto a single worker task."""

    def __init__(self, *, reconcile: Callable[[], Awaitable[None]]) -> None:
        self._reconcile = reconcile
        self._pending = False
        self._task: asyncio.Task[None] | None = None

    def request(self) -> None:
        """Request a reconcile pass."""
        self._pending = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self._pending:
            self._pending = False
            await self._safely(self._reconcile, "plugin reconcile")

    @staticmethod
    async def _safely(work: Callable[[], Awaitable[None]], label: str) -> None:
        try:
            await work()
        except Exception as exc:  # noqa: BLE001 - a store change must never crash the runtime
            logger.warning("%s failed: %s", label, exc)

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
