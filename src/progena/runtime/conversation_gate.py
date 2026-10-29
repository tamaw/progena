"""Runtime-shared serialization for checkpointed conversation turns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

_T = TypeVar("_T")


@dataclass
class _ConversationEntry:
    tail: asyncio.Future[None] | None = None
    owner_task: asyncio.Task[Any] | None = None
    requests: int = 0


class ConversationTurnGate:
    """Serialize mutations of one checkpoint thread without blocking other threads.

    The gate is held around a runnable's complete graph invocation, rather than
    around individual checkpoint reads or writes.  That keeps an HTTP turn, a
    heartbeat follow-up, and an object-agent continuation from branching the
    same LangGraph checkpoint concurrently.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _ConversationEntry] = {}

    def is_active(self, thread_id: str) -> bool:
        """Return whether a turn is running or queued for ``thread_id``."""
        entry = self._entries.get(thread_id)
        return entry is not None and entry.requests > 0

    def _complete_request(
        self,
        thread_id: str,
        entry: _ConversationEntry,
        completion: asyncio.Future[None],
    ) -> None:
        if completion.done():
            return
        completion.set_result(None)
        entry.requests -= 1
        if entry.requests == 0 and self._entries.get(thread_id) is entry:
            self._entries.pop(thread_id, None)

    async def run(
        self,
        thread_id: str,
        turn: Callable[[], Awaitable[_T]],
        *,
        on_queued: Callable[[], Awaitable[None]] | None = None,
    ) -> _T:
        """Run ``turn`` in FIFO order with other work for the same checkpoint thread."""
        entry = self._entries.get(thread_id)
        current_task = asyncio.current_task()
        if entry is not None and current_task is not None and entry.owner_task is current_task:
            # A graph can invoke a same-thread helper synchronously. It is
            # already protected by this task's outer turn, so waiting for its
            # own completion would deadlock.
            return await turn()
        if entry is None:
            entry = _ConversationEntry()
            self._entries[thread_id] = entry
        predecessor = entry.tail
        completion: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry.tail = completion
        entry.requests += 1
        try:
            if predecessor is not None and on_queued is not None:
                await on_queued()
            if predecessor is not None:
                # A cancelled waiter must not cancel the preceding turn's
                # completion signal. Its own finally unblocks its successor.
                await asyncio.shield(predecessor)
            entry.owner_task = current_task
            return await turn()
        finally:
            if entry.owner_task is current_task:
                entry.owner_task = None
            if predecessor is not None and not predecessor.done():
                # A cancelled waiter must retain its place in the dependency
                # chain until its predecessor finishes; otherwise a later
                # waiter could run alongside that predecessor.
                predecessor.add_done_callback(
                    lambda _: self._complete_request(thread_id, entry, completion)
                )
            else:
                self._complete_request(thread_id, entry, completion)
