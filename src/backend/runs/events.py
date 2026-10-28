"""Live run projections and connection-scoped, shared trace reconstruction."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Any

from src.backend.events import stream
from src.backend.events.documents import TypedDocumentEvent
from src.backend.storage.documents import StoreChangeListener, store_database_url
from src.progena.routines.events import RUN_EVENT_PREFIX

from .models import RunKind, RunTrace
from .service import build_trace

logger = logging.getLogger(__name__)
RUN_CHECKPOINT_CHANNEL = "run_checkpoints"
_DEBOUNCE_SECONDS = 0.5
_CACHE_SIZE = 128
_TERMINAL = {"completed", "failed", "cancelled", "stopped", "error", "interrupted"}
TraceKey = tuple[RunKind, str]
TraceResult = tuple[RunTrace, set[str]]


@dataclass
class _Interest:
    kind: RunKind
    run_id: str
    token: object


class RunsEvents:
    """Build once per run/version; connection tokens prevent stale deliveries."""

    def __init__(self) -> None:
        self.interests: dict[str, _Interest] = {}
        self.by_thread: dict[str, set[TraceKey]] = defaultdict(set)
        self.trace_threads: dict[TraceKey, set[str]] = {}
        self.listener: StoreChangeListener | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.pending: dict[TraceKey, asyncio.Task[Any]] = {}
        self.builds: dict[TraceKey, asyncio.Task[TraceResult]] = {}
        self.versions: dict[TraceKey, int] = defaultdict(int)
        self.dirty: set[TraceKey] = set()
        self.completed: OrderedDict[TraceKey, TraceResult] = OrderedDict()
        self.store: Any = None
        self.row_threads: set[str] = set()
        self.row_task: asyncio.Task[Any] | None = None

    def _spawn(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self._report_failure)
        return task

    @staticmethod
    def _report_failure(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("Runs refresh failed", exc_info=(type(error), error, error.__traceback__))

    async def start(self, store: Any) -> None:
        self.store = store
        database_url = store_database_url(store)
        if database_url and self.listener is None:
            self.listener = StoreChangeListener(
                database_url,
                channel=RUN_CHECKPOINT_CHANNEL,
                on_change=self.checkpoint_changed,
                on_resync=self.invalidate_all,
            )
            await self.listener.start()

    async def stop(self) -> None:
        if self.listener is not None:
            await self.listener.stop()
            self.listener = None
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.pending.clear()
        self.builds.clear()
        self.dirty.clear()
        self.row_threads.clear()
        self.row_task = None
        self.interests.clear()
        self.by_thread.clear()
        self.trace_threads.clear()
        self.completed.clear()
        self.versions.clear()
        self.store = None

    def _watching(self, key: TraceKey) -> bool:
        return any((interest.kind, interest.run_id) == key for interest in self.interests.values())

    def _index(self, key: TraceKey, threads: set[str]) -> None:
        for thread_id in self.trace_threads.get(key, set()) - threads:
            self.by_thread[thread_id].discard(key)
            if not self.by_thread[thread_id]:
                del self.by_thread[thread_id]
        for thread_id in threads:
            self.by_thread[thread_id].add(key)
        self.trace_threads[key] = set(threads)

    def _prune(self, key: TraceKey) -> None:
        if self._watching(key) or key in self.completed or key in self.builds:
            return
        self._index(key, set())
        self.trace_threads.pop(key, None)
        self.versions.pop(key, None)

    def disconnected(self, client_id: str) -> None:
        self.unsubscribe(client_id)

    def has_interest(self, client_id: str, token: object) -> bool:
        interest = self.interests.get(client_id)
        return interest is not None and interest.token is token

    def unsubscribe(self, client_id: str) -> None:
        old = self.interests.pop(client_id, None)
        if old is not None:
            self._prune((old.kind, old.run_id))

    def _cache(self, key: TraceKey, result: TraceResult) -> None:
        trace, threads = result
        if trace.status not in _TERMINAL:
            self.completed.pop(key, None)
        else:
            self.completed[key] = result
            self.completed.move_to_end(key)
            while len(self.completed) > _CACHE_SIZE:
                evicted, _ = self.completed.popitem(last=False)
                self._prune(evicted)
        if self._watching(key) or key in self.completed:
            self._index(key, threads)

    async def _build_current(self, store: Any, key: TraceKey) -> TraceResult:
        while True:
            version = self.versions[key]
            result = await build_trace(store, *key)
            if self.versions[key] == version:
                self._cache(key, result)
                return result

    async def read_trace(self, store: Any, kind: RunKind, run_id: str) -> TraceResult:
        key = (kind, run_id)
        if key in self.completed:
            self.completed.move_to_end(key)
            return self.completed[key]
        task = self.builds.get(key)
        if task is None:
            task = self._spawn(self._build_current(store, key))
            self.builds[key] = task

            def release(completed: asyncio.Task[Any]) -> None:
                if self.builds.get(key) is completed:
                    self.builds.pop(key, None)
                    self._prune(key)

            task.add_done_callback(release)
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.builds.get(key) is task:
                self.builds.pop(key, None)
                self._prune(key)

    async def subscribe(
        self, store: Any, client_id: str, kind: RunKind, run_id: str
    ) -> tuple[RunTrace, object] | None:
        self.unsubscribe(client_id)
        token = object()
        self.interests[client_id] = _Interest(kind, run_id, token)
        try:
            trace, threads = await self.read_trace(store, kind, run_id)
        except BaseException:
            if self.has_interest(client_id, token):
                self.unsubscribe(client_id)
            raise
        if not self.has_interest(client_id, token):
            return None
        self._index((kind, run_id), threads)
        return trace, token

    def _invalidate(self, key: TraceKey) -> None:
        if key in self.completed or key in self.builds or self._watching(key):
            self.versions[key] += 1
        self.completed.pop(key, None)
        self.schedule(key)
        self._prune(key)

    def checkpoint_changed(self, notification: str) -> None:
        try:
            thread_id = json.loads(notification)["thread_id"]
        except (TypeError, ValueError, KeyError):
            logger.warning("Invalid run checkpoint notification")
            return
        if not isinstance(thread_id, str):
            return
        for key in tuple(self.by_thread.get(thread_id, ())):
            self._invalidate(key)
        if thread_id.startswith("routine:"):
            self.row_threads.add(thread_id)
            if self.row_task is None:
                self.row_task = self._spawn(self._drain_rows())

    async def _drain_rows(self) -> None:
        try:
            while self.row_threads:
                await asyncio.sleep(_DEBOUNCE_SECONDS)
                threads = self.row_threads
                self.row_threads = set()
                for thread_id in threads:
                    await self._checkpoint_row(thread_id)
        finally:
            self.row_task = None

    async def _checkpoint_row(self, thread_id: str) -> None:
        from .service import checkpoint_row_changed

        if self.store is not None:
            for row in await checkpoint_row_changed(self.store, thread_id):
                stream.broadcast_ui_event("run.updated", {"run": row.model_dump(mode="json")})

    def invalidate_all(self) -> None:
        keys: set[TraceKey] = set(self.completed) | set(self.builds)
        keys.update((interest.kind, interest.run_id) for interest in self.interests.values())
        for key in keys:
            self._invalidate(key)

    def schedule(self, key: TraceKey) -> None:
        if not self._watching(key):
            return
        self.dirty.add(key)
        if key not in self.pending:
            self.pending[key] = self._spawn(self._drain(key))

    async def _drain(self, key: TraceKey) -> None:
        try:
            while key in self.dirty:
                self.dirty.discard(key)
                await asyncio.sleep(_DEBOUNCE_SECONDS)
                await self.refresh(key)
        finally:
            self.pending.pop(key, None)
            self._prune(key)

    async def refresh(self, key: TraceKey) -> None:
        viewers = [
            (client_id, i.token)
            for client_id, i in self.interests.items()
            if (i.kind, i.run_id) == key
        ]
        if not viewers or self.store is None:
            return
        trace, threads = await self.read_trace(self.store, *key)
        if self._watching(key) or key in self.completed:
            self._index(key, threads)
        for client_id, token in viewers:
            if self.has_interest(client_id, token):
                stream.broadcast_ui_event(
                    "run.trace.updated",
                    {"trace": trace.model_dump(mode="json")},
                    client_id=client_id,
                    interest=token,
                )

    async def _broadcast_row(self, kind: RunKind, run_id: str) -> None:
        from .service import get_run_row

        if self.store is not None:
            row = await get_run_row(self.store, kind, run_id)
            if row is not None:
                stream.broadcast_ui_event("run.updated", {"run": row.model_dump(mode="json")})

    def _deleted(self, key: TraceKey) -> None:
        self._invalidate(key)
        stream.broadcast_ui_event("run.deleted", {"kind": key[0], "run_id": key[1]})
        for client_id, interest in tuple(self.interests.items()):
            if (interest.kind, interest.run_id) == key:
                self.unsubscribe(client_id)
        self._prune(key)

    async def document_event(self, typed: TypedDocumentEvent) -> None:
        event = typed.event
        if event.key == "schedule_runs":
            before = (
                {
                    str(item.get("run_id")): item
                    for item in event.before or []
                    if isinstance(item, dict)
                }
                if isinstance(event.before, list)
                else {}
            )
            after = (
                {
                    str(item.get("run_id")): item
                    for item in event.after or []
                    if isinstance(item, dict)
                }
                if isinstance(event.after, list)
                else {}
            )
            for run_id in before.keys() | after.keys():
                if before.get(run_id) == after.get(run_id):
                    continue
                key: TraceKey = ("schedule", run_id)
                if run_id not in after:
                    self._deleted(key)
                else:
                    self._invalidate(key)
                    await self._broadcast_row(*key)
            return
        if event.key.startswith("workflow_run:"):
            key = ("workflow", event.key.removeprefix("workflow_run:"))
        elif event.key.startswith(RUN_EVENT_PREFIX):
            payload = event.after if isinstance(event.after, dict) else event.before
            if not isinstance(payload, dict):
                return
            parent = payload.get("parent")
            if isinstance(parent, dict):
                for parent_key in tuple(self.by_thread.get(str(parent.get("thread_id") or ""), ())):
                    self._invalidate(parent_key)
            request_id = str(payload.get("request_id") or "")
            if not request_id:
                return
            key = ("routine", request_id)
        else:
            return
        self._invalidate(key)
        if event.op == "deleted":
            # Removing one immutable lifecycle event need not remove the entire run.
            from .service import get_run_row

            row = await get_run_row(self.store, *key) if self.store is not None else None
            if row is None:
                self._deleted(key)
            else:
                stream.broadcast_ui_event("run.updated", {"run": row.model_dump(mode="json")})
            return
        await self._broadcast_row(*key)


runs_events = RunsEvents()
