"""Single-flight, coalescing refreshes of live object-agent registrations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class AgentSyncScheduler:
    """Collapse a notification burst into one refresh and one trailing pass."""

    def __init__(self, *, reconcile: Callable[[], Awaitable[None]]) -> None:
        self._refresh = reconcile
        self._pending = False
        self._task: asyncio.Task[None] | None = None

    def request(self) -> None:
        self._pending = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self._pending:
            self._pending = False
            try:
                await self._refresh()
            except Exception:
                logger.exception("Object-agent registration refresh failed")

    async def aclose(self) -> None:
        self._pending = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
