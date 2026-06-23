"""Coalescing, single-flight scheduler for live MCP runtime re-syncs.

Store-change notifications (UI server/tool toggles, agent edits to
``/state/mcp.json``) can arrive in bursts. Running a full re-sync per
notification would both waste work and — worse — let two re-syncs interleave a
teardown (``mcp_runtime.close()``) with a rebuild on the shared runtime.

This scheduler guarantees:
- **Single-flight**: at most one operation runs at a time, so concurrent
  notifications can't race on the runtime.
- **Coalescing**: a burst collapses to the minimum work; a pending full re-sync
  subsumes a pending lightweight re-apply (a full re-sync re-applies flags too).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class MCPSyncScheduler:
    """Serialize and coalesce MCP re-sync work onto a single worker task."""

    def __init__(
        self,
        *,
        full_resync: Callable[[], Awaitable[None]],
        reapply: Callable[[], Awaitable[None]],
    ) -> None:
        self._full_resync = full_resync
        self._reapply = reapply
        self._full_pending = False
        self._reapply_pending = False
        self._task: asyncio.Task[None] | None = None

    def request(self, *, full: bool) -> None:
        """Request work. ``full=True`` for a config change (re-discovery);
        ``full=False`` for a per-tool enable toggle (lightweight re-apply)."""
        if full:
            self._full_pending = True
        else:
            self._reapply_pending = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while self._full_pending or self._reapply_pending:
            if self._full_pending:
                # A full re-sync re-applies enable flags too, so it subsumes a
                # pending re-apply.
                self._full_pending = False
                self._reapply_pending = False
                await self._safely(self._full_resync, "MCP full re-sync")
            elif self._reapply_pending:
                self._reapply_pending = False
                await self._safely(self._reapply, "MCP flag re-apply")

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
