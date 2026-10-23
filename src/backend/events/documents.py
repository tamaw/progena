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
from datetime import timedelta
from typing import Any

from src.backend.storage.documents import (
    DOCUMENT_EVENT_CHANNEL,
    EVENT_LOG_START,
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    DocumentEvent,
    DocumentEventPosition,
    InMemoryDocumentStore,
    StoreChangeListener,
    store_database_url,
)
from src.progena.documents.kinds import DOCUMENT_TYPE_KEY_PREFIX
from src.progena.documents.types import (
    DocumentType,
    builtin_document_type_rows,
    parse_document_types,
    resolve_document_type,
)
from src.progena.workflows.events import build_document_event_output

logger = logging.getLogger(__name__)

DISPATCH_CONSUMER = "backend.dispatch"
_BATCH_SIZE = 200
_EVENT_RETENTION = timedelta(days=1)
_PRUNE_INTERVAL_SECONDS = 600.0
# How often to re-check while committed events wait behind an open transaction.
_DEFERRED_RETRY_SECONDS = 1.0


def list_document_types(store: BackendDocumentStore) -> dict[str, DocumentType]:
    """Return the document type registry stored in Postgres, keyed by id."""

    rows = store.list_documents(PROGENA_STATE_PREFIX, DOCUMENT_TYPE_KEY_PREFIX)
    return parse_document_types(document.payload for _key, document in rows)


def seed_document_types(store: BackendDocumentStore) -> None:
    """Write the built-in document types, touching only rows that differ."""

    for key, payload in builtin_document_type_rows():
        existing = store.get_document(PROGENA_STATE_PREFIX, key, None)
        if existing is None or existing.payload != payload:
            store.save_document(PROGENA_STATE_PREFIX, key, payload)


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
        self._stopped = False

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
        self._stopped = False
        # Bind the wake signal to this loop; a dispatcher restarted under a new
        # loop (tests, app reload) must not await an Event from the old one.
        self._wake = asyncio.Event()
        store = self._store_getter()
        if store.get_document_event_cursor(self._consumer) is None:
            store.save_document_event_cursor(self._consumer, store.latest_document_event_position())
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

    @property
    def stopped(self) -> bool:
        """True once a dispatch failure has stopped this consumer."""

        return self._stopped

    async def _run(self) -> None:
        deferred = False
        while True:
            if deferred:
                # Committed events wait behind an open transaction. Its end
                # produces no event of its own, so re-check on a timer.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), _DEFERRED_RETRY_SECONDS)
            else:
                await self._wake.wait()
            self._wake.clear()
            try:
                deferred = await self.drain()
            except Exception:
                # Fail fast: the failing event stays unacknowledged and nothing
                # after it is dispatched until the backend is fixed/restarted.
                logger.exception("Document event dispatch failed; dispatcher stopped")
                self._stopped = True
                return

    async def drain(self) -> bool:
        """Process every committed event after the cursor, in commit order.

        Handler exceptions propagate; the cursor only advances past events
        every handler accepted. Workflow-level failures are recorded by the
        handler as failed runs and do not raise. Returns True when committed
        events remain deferred behind an older open transaction.
        """

        async with self._lock:
            store = self._store_getter()
            cursor = store.get_document_event_cursor(self._consumer) or EVENT_LOG_START
            while True:
                events = store.read_document_events(cursor, _BATCH_SIZE)
                if not events:
                    break
                for event in events:
                    await self._dispatch(event)
                    cursor = event.position
                    store.save_document_event_cursor(self._consumer, cursor)
            self._maybe_prune(store, cursor)
            return store.has_deferred_document_events(cursor)

    def _maybe_prune(self, store: BackendDocumentStore, cursor: DocumentEventPosition) -> None:
        now = time.monotonic()
        if now - self._last_prune < _PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        removed = store.prune_document_events(through=cursor, older_than=_EVENT_RETENTION)
        if removed:
            logger.info("Pruned %d processed document event(s)", removed)

    async def _dispatch(self, event: DocumentEvent) -> None:
        if event.prefix == PROGENA_STATE_PREFIX and event.key.startswith(DOCUMENT_TYPE_KEY_PREFIX):
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
            await handler(typed)
