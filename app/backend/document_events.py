"""Durable document event dispatch for the backend.

Consumes the ``document_events`` log (see
``PostgresDocumentStore._ensure_document_event_log``) from a persisted cursor,
so events written while the backend was down or disconnected are processed on
the next catch-up instead of being lost. NOTIFY on ``document_events`` (or the
in-memory store's listener hook) only wakes the consumer up.

Each event is resolved to its registered document type (``document_type:<id>``
rows) and handed to every registered handler: workflow triggers, event
widgets, and task activity all subscribe the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.progenitor.document_kinds import DOCUMENT_TYPE_KEY_PREFIX
from app.progenitor.document_types import (
    DocumentType,
    builtin_document_type_rows,
    parse_document_types,
    resolve_document_type,
)
from app.progenitor.workflows import build_document_event_output

from .document_store import (
    DOCUMENT_EVENT_CHANNEL,
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    DocumentEvent,
    InMemoryDocumentStore,
    StoreChangeListener,
    store_database_url,
)

logger = logging.getLogger(__name__)

DISPATCH_CONSUMER = "backend.dispatch"
_BATCH_SIZE = 200
# A missing sequence number is either a transaction that has not committed yet
# or a rolled-back insert (a permanent gap). Wait this long for it before
# moving past it.
_SEQUENCE_GAP_GRACE = timedelta(seconds=5)
_RETRY_DELAY_SECONDS = 1.0
_EVENT_RETENTION = timedelta(days=1)
_PRUNE_INTERVAL_SECONDS = 600.0


def list_document_types(store: BackendDocumentStore) -> dict[str, DocumentType]:
    """Return the document type registry stored in Postgres, keyed by id."""

    rows = store.list_documents(PROGENITOR_STATE_PREFIX, DOCUMENT_TYPE_KEY_PREFIX)
    return parse_document_types(document.payload for _key, document in rows)


def seed_document_types(store: BackendDocumentStore) -> None:
    """Write the built-in document types, touching only rows that differ."""

    for key, payload in builtin_document_type_rows():
        existing = store.get_document(PROGENITOR_STATE_PREFIX, key, None)
        if existing is None or existing.payload != payload:
            store.save_document(PROGENITOR_STATE_PREFIX, key, payload)


@dataclass(frozen=True)
class TypedDocumentEvent:
    """One log event plus its resolved type and workflow-facing output object."""

    event: DocumentEvent
    document_type: DocumentType | None
    # The event node output object; None for keys outside every document type.
    output: dict[str, Any] | None


DocumentEventHandler = Callable[[TypedDocumentEvent], Awaitable[None]]


class DocumentEventDispatcher:
    """Single consumer of the document event log with a persisted cursor."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        consumer: str = DISPATCH_CONSUMER,
    ) -> None:
        self._store_getter = store_getter
        self._consumer = consumer
        self._handlers: list[DocumentEventHandler] = []
        self._types: dict[str, DocumentType] | None = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._listener: StoreChangeListener | None = None
        self._memory_store: InMemoryDocumentStore | None = None
        self._last_prune = 0.0

    def add_handler(self, handler: DocumentEventHandler) -> None:
        self._handlers.append(handler)

    def document_types(self) -> dict[str, DocumentType]:
        """Return the cached type registry, loading it on first use."""

        if self._types is None:
            self._types = list_document_types(self._store_getter())
        return self._types

    async def start(self, reconnect_delay: float = 2.0) -> None:
        """Begin consuming: new consumers start at the log head, not its history."""

        if self._task is not None and not self._task.done():
            return
        self._loop = asyncio.get_running_loop()
        # Bind the wake signal to this loop; a dispatcher restarted under a new
        # loop (tests, app reload) must not await an Event from the old one.
        self._wake = asyncio.Event()
        store = self._store_getter()
        if store.get_document_event_cursor(self._consumer) is None:
            store.save_document_event_cursor(self._consumer, store.latest_document_event_seq())
        database_url = store_database_url(store)
        if database_url is not None:
            self._listener = StoreChangeListener(
                database_url,
                channel=DOCUMENT_EVENT_CHANNEL,
                on_change=lambda _payload: self.wake(),
                on_resync=self.wake,
                reconnect_delay=reconnect_delay,
            )
            await self._listener.start()
        elif isinstance(store, InMemoryDocumentStore):
            self._memory_store = store
            store.add_document_event_listener(self._on_memory_event)
        self._task = asyncio.create_task(self._run())
        self.wake()

    async def stop(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
        if self._memory_store is not None:
            self._memory_store.remove_document_event_listener(self._on_memory_event)
            self._memory_store = None
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _on_memory_event(self, _seq: int) -> None:
        self.wake()

    def wake(self) -> None:
        """Request a catch-up pass; safe to call from any thread."""

        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake.set()
        else:
            loop.call_soon_threadsafe(self._wake.set)

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                retry = await self.drain()
            except Exception:
                logger.exception("Document event dispatch failed; retrying")
                retry = True
            if retry:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
                self._wake.set()

    async def drain(self) -> bool:
        """Process every committed event after the cursor.

        Returns True when a sequence gap younger than the grace period stopped
        the pass, so the caller should retry shortly.
        """

        async with self._lock:
            store = self._store_getter()
            cursor = store.get_document_event_cursor(self._consumer) or 0
            while True:
                events = store.read_document_events(cursor, _BATCH_SIZE)
                if not events:
                    break
                for event in events:
                    if (
                        event.seq != cursor + 1
                        and datetime.now(UTC) - event.created_at < _SEQUENCE_GAP_GRACE
                    ):
                        return True
                    await self._dispatch(event)
                    cursor = event.seq
                    store.save_document_event_cursor(self._consumer, cursor)
            self._maybe_prune(store, cursor)
        return False

    def _maybe_prune(self, store: BackendDocumentStore, cursor: int) -> None:
        now = time.monotonic()
        if now - self._last_prune < _PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        removed = store.prune_document_events(through_seq=cursor, older_than=_EVENT_RETENTION)
        if removed:
            logger.info("Pruned %d processed document event(s)", removed)

    async def _dispatch(self, event: DocumentEvent) -> None:
        if event.prefix == PROGENITOR_STATE_PREFIX and event.key.startswith(
            DOCUMENT_TYPE_KEY_PREFIX
        ):
            self._types = None
        document_type = resolve_document_type(event.key, self.document_types())
        output = (
            build_document_event_output(
                event_id=event.seq,
                document_type=document_type,
                key=event.key,
                op=event.op,
                before=event.before,
                after=event.after,
                timestamp=event.created_at.isoformat(),
            )
            if document_type is not None
            else None
        )
        typed = TypedDocumentEvent(event=event, document_type=document_type, output=output)
        for handler in list(self._handlers):
            try:
                await handler(typed)
            except Exception:
                logger.exception(
                    "Document event handler failed for %s %s (seq %d)",
                    event.op,
                    event.key,
                    event.seq,
                )
