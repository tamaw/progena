"""Runtime-side Postgres LISTEN/NOTIFY listener for ``store`` table changes.

Mirrors the backend ``StoreChangeListener`` so the runtime can react to writes
made by another process (e.g. a UI tool toggle that flips an ``enabled`` flag in
the ``mcp_tools`` document) without polling. Fully event-driven, with reconnect
backoff across DB blips.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

# Must match the channel of the trigger installed by the backend document store
# (src/backend/storage/documents.py STORE_CHANGE_CHANNEL).
STORE_CHANGE_CHANNEL = "store_changed"


class RuntimeStoreListener:
    """Stream ``store`` change notifications to ``on_change(payload)``."""

    def __init__(
        self,
        database_url: str,
        *,
        on_change: Callable[[str], None],
        on_resync: Callable[[], None] | None = None,
        reconnect_delay: float = 2.0,
    ) -> None:
        self._database_url = database_url
        self._on_change = on_change
        self._on_resync = on_resync
        self._reconnect_delay = reconnect_delay
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - keep listening across DB blips
                logger.warning(
                    "Runtime store listener disconnected (%s); reconnecting in %ss",
                    exc,
                    self._reconnect_delay,
                )
                try:
                    await asyncio.sleep(self._reconnect_delay)
                except asyncio.CancelledError:
                    break

    async def _listen_once(self) -> None:
        aconn = await psycopg.AsyncConnection.connect(self._database_url, autocommit=True)
        try:
            await aconn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(STORE_CHANGE_CHANNEL)))
            logger.info("Runtime listening for store changes on %r", STORE_CHANGE_CHANNEL)
            # Recover any change missed while disconnected before streaming live.
            if self._on_resync is not None:
                self._on_resync()
            async for notify in aconn.notifies():
                self._on_change(notify.payload)
        finally:
            await aconn.close()
