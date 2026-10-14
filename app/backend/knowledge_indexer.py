"""Backend knowledge search indexer: keeps ``knowledge_chunks`` in step with pages.

Triggered by the store change feed (page writes, catalog / indexer / profile
changes, listener resync) with a short per-base debounce, processed by one
asyncio worker. A Postgres advisory lock held for the indexer's lifetime keeps a
single indexer across backend processes. Only pages whose content hash differs
from the index are embedded. Progress goes to the ``knowledge_index`` status
document (``/knowledge/indexer.json``), the UI (``knowledge.index.*`` events),
and workflows (``knowledge.index.completed|failed`` triggers).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import psycopg

from app.progenitor.knowledge.embedding import Embedder, embedder_for
from app.progenitor.knowledge.index import (
    PageChunk,
    chunk_count,
    chunk_page,
    delete_other_bases,
    delete_page_chunks,
    embedding_text,
    ensure_index_schema,
    indexed_shas,
    is_indexable,
    page_sha,
    pending_paths,
    replace_page_chunks,
)
from app.progenitor.knowledge.indexer_config import (
    DEFAULT_DIMENSIONS,
    INDEX_STATUS_DOC_KEY,
    INDEXER_DOC_KEY,
    EmbeddingTarget,
    resolve_embedding_target,
)
from app.progenitor.utils import utcnow_isoformat

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store, store_database_url
from .knowledge_service import load_bases

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = "progenitor_knowledge_indexer"
_DEBOUNCE_SECONDS = 2.0
_ALL = "*"
SETTINGS_KEYS = frozenset({"knowledge", INDEXER_DOC_KEY, "profile"})

EmbedderFactory = Callable[[EmbeddingTarget], Embedder]
Publisher = Callable[[str, dict[str, Any]], None]
Dispatcher = Callable[[str, dict[str, Any]], Awaitable[None]]


def _payload(key: str) -> Any:
    row = get_default_document_store().get_document(PROGENITOR_STATE_PREFIX, key)
    return row.payload if row is not None else None


def _save_status(doc: dict[str, Any]) -> None:
    get_default_document_store().save_document(PROGENITOR_STATE_PREFIX, INDEX_STATUS_DOC_KEY, doc)


def _status_doc() -> dict[str, Any]:
    doc = _payload(INDEX_STATUS_DOC_KEY)
    doc = dict(doc) if isinstance(doc, dict) else {}
    doc["bases"] = dict(doc.get("bases") or {})
    doc.setdefault("embedding", None)
    return doc


def _live_pages(base_id: str) -> dict[str, str]:
    prefix = f"knowledge:{base_id}/"
    live: dict[str, str] = {}
    for key, document in get_default_document_store().list_documents(
        PROGENITOR_STATE_PREFIX, prefix
    ):
        payload = document.payload if isinstance(document.payload, dict) else {}
        path = key[len(prefix) :]
        content = payload.get("content")
        if isinstance(content, str) and is_indexable(path, content):
            live[path] = content
    return live


def _default_publish(event_type: str, payload: dict[str, Any]) -> None:
    from .ui_routes import broadcast_ui_event

    broadcast_ui_event(event_type, payload)


async def _default_dispatch(event_type: str, payload: dict[str, Any]) -> None:
    from .ui_routes import get_workflow_executor

    await get_workflow_executor().dispatch_event(
        event_type, {"event_type": event_type, "payload": payload}
    )


class KnowledgeIndexer:
    """Reconcile live knowledge pages with the ``knowledge_chunks`` search index."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        embedder_factory: EmbedderFactory = embedder_for,
        publish: Publisher = _default_publish,
        dispatch: Dispatcher = _default_dispatch,
        debounce_seconds: float = _DEBOUNCE_SECONDS,
    ) -> None:
        self._url = database_url
        self._embedder_factory = embedder_factory
        self._publish = publish
        self._dispatch = dispatch
        self._debounce = debounce_seconds
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._worker: asyncio.Task[None] | None = None
        self._lock_conn: psycopg.Connection | None = None

    @property
    def running(self) -> bool:
        return self._worker is not None

    def _database_url(self) -> str:
        url = self._url or store_database_url(get_default_document_store())
        if not url:
            raise RuntimeError("knowledge indexer needs a Postgres database")
        return url

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self._worker is not None:
            return
        url = self._url or store_database_url(get_default_document_store())
        if not url:
            logger.info("Knowledge indexer disabled: no Postgres document store")
            return
        if not await asyncio.to_thread(self._acquire_lock, url):
            logger.info("Knowledge indexer idle: another backend process holds the lock")
            return
        self._url = url
        self._worker = asyncio.create_task(self._run(), name="knowledge-indexer")
        self.schedule_all()
        logger.info("Knowledge indexer started")

    async def stop(self) -> None:
        for timer in self._timers.values():
            timer.cancel()
        self._timers.clear()
        if self._worker is not None:
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self._lock_conn is not None:
            conn, self._lock_conn = self._lock_conn, None
            await asyncio.to_thread(conn.close)

    def _acquire_lock(self, url: str) -> bool:
        conn = psycopg.connect(url, autocommit=True)
        row = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))", (_ADVISORY_LOCK_KEY,)
        ).fetchone()
        if row and row[0]:
            self._lock_conn = conn
            return True
        conn.close()
        return False

    # ------------------------------------------------------------------ triggers
    def on_store_change(self, key: str | None) -> None:
        """Route a store change key: a page → its base; settings or resync → every base."""
        if key is None or key in SETTINGS_KEYS:
            self.schedule_all()
        elif key.startswith("knowledge:"):
            base_id = key[len("knowledge:") :].split("/", 1)[0]
            if base_id:
                self.schedule(base_id)

    def schedule(self, base_id: str) -> None:
        if self._worker is None:
            return
        if (timer := self._timers.pop(base_id, None)) is not None:
            timer.cancel()
        loop = asyncio.get_running_loop()
        self._timers[base_id] = loop.call_later(self._debounce, self._enqueue, base_id)

    def schedule_all(self) -> None:
        self.schedule(_ALL)

    def _enqueue(self, item: str) -> None:
        self._timers.pop(item, None)
        self._queue.put_nowait(item)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item == _ALL:
                    await self.reconcile_all()
                else:
                    await self.reconcile(item)
            except Exception:
                logger.exception("Knowledge indexer failed on %r", item)

    # ------------------------------------------------------------------ reconcile
    async def reconcile_all(self) -> None:
        """Drop chunks/status of removed bases, then reconcile every catalog base."""
        catalog = [str(base.get("id")) for base in await asyncio.to_thread(load_bases)]
        await asyncio.to_thread(self._drop_removed_bases, catalog)
        for base_id in catalog:
            await self.reconcile(base_id)

    def _drop_removed_bases(self, catalog: list[str]) -> None:
        with psycopg.connect(self._database_url()) as conn:
            if ensure_index_schema(conn, self._configured_dimensions()):
                _save_status({**_status_doc(), "bases": {}})
            delete_other_bases(conn, catalog)
        doc = _status_doc()
        kept = {base_id: rec for base_id, rec in doc["bases"].items() if base_id in catalog}
        if len(kept) != len(doc["bases"]):
            _save_status({**doc, "bases": kept})

    @staticmethod
    def _configured_dimensions() -> int:
        settings = _payload(INDEXER_DOC_KEY)
        try:
            return int((settings or {}).get("dimensions") or DEFAULT_DIMENSIONS)
        except (TypeError, ValueError):
            return DEFAULT_DIMENSIONS

    async def reconcile(self, base_id: str) -> None:
        """Embed changed pages of one base and drop chunks of removed pages."""
        catalog = {str(base.get("id")) for base in await asyncio.to_thread(load_bases)}
        if base_id not in catalog:
            return
        try:
            target = resolve_embedding_target(_payload(INDEXER_DOC_KEY), _payload("profile"))
        except ValueError as exc:
            await self._finish(base_id, self._failed_record(base_id, str(exc)))
            return
        await asyncio.to_thread(self._ensure_target, target)
        live = await asyncio.to_thread(_live_pages, base_id)
        indexed = await asyncio.to_thread(self._indexed, base_id)
        pending = pending_paths(live, indexed)
        removed = sorted(set(indexed) - set(live))
        previous = _status_doc()["bases"].get(base_id) or {}
        if not pending and not removed and previous.get("status") == "indexed":
            if previous.get("pages") != len(live):
                await self._write(base_id, {**previous, "pages": len(live)}, None)
            return

        record: dict[str, Any] = {
            "status": "indexing",
            "pages": len(live),
            "indexed_pages": len(live) - len(pending),
            "chunks": previous.get("chunks") or 0,
            "pending": pending,
            "error": "",
            "started_at": utcnow_isoformat(),
            "indexed_at": previous.get("indexed_at"),
        }
        await self._write(base_id, record, "knowledge.index.started")
        try:
            embedder = self._embedder_factory(target)
            for path in pending:
                front_matter, chunks = chunk_page(live[path])
                vectors = await embedder.embed([embedding_text(front_matter, c) for c in chunks])
                await asyncio.to_thread(
                    self._store_page,
                    base_id,
                    path,
                    page_sha(live[path]),
                    str(front_matter.get("title") or ""),
                    chunks,
                    vectors,
                )
                record["indexed_pages"] += 1
                record["pending"] = [p for p in record["pending"] if p != path]
                await self._write(base_id, record, "knowledge.index.progress")
            chunks_total = await asyncio.to_thread(self._finish_base, base_id, removed)
        except Exception as exc:
            logger.warning("Knowledge indexing failed for %s: %s", base_id, exc, exc_info=True)
            await self._finish(base_id, {**record, "status": "failed", "error": str(exc)})
            return
        await self._finish(
            base_id,
            {
                **record,
                "status": "indexed",
                "chunks": chunks_total,
                "pending": [],
                "indexed_at": utcnow_isoformat(),
            },
        )

    def _ensure_target(self, target: EmbeddingTarget) -> None:
        """Rebuild the index when the embedding deployment or dimensions change."""
        doc = _status_doc()
        changed = doc["embedding"] != target.as_dict()
        with psycopg.connect(self._database_url()) as conn:
            recreated = ensure_index_schema(conn, target.dimensions)
            if changed and not recreated:
                conn.execute("TRUNCATE knowledge_chunks")
                conn.commit()
        if changed or recreated:
            _save_status({**doc, "embedding": target.as_dict(), "bases": {}})

    def _indexed(self, base_id: str) -> dict[str, str]:
        with psycopg.connect(self._database_url()) as conn:
            return indexed_shas(conn, base_id)

    def _store_page(
        self,
        base_id: str,
        path: str,
        sha: str,
        title: str,
        chunks: list[PageChunk],
        vectors: list[list[float]],
    ) -> None:
        with psycopg.connect(self._database_url()) as conn:
            replace_page_chunks(conn, base_id, path, sha, title, chunks, vectors)

    def _finish_base(self, base_id: str, removed: list[str]) -> int:
        with psycopg.connect(self._database_url()) as conn:
            delete_page_chunks(conn, base_id, removed)
            return chunk_count(conn, base_id)

    @staticmethod
    def _failed_record(base_id: str, error: str) -> dict[str, Any]:
        previous = _status_doc()["bases"].get(base_id) or {}
        return {
            "status": "failed",
            "pages": previous.get("pages") or 0,
            "indexed_pages": previous.get("indexed_pages") or 0,
            "chunks": previous.get("chunks") or 0,
            "pending": previous.get("pending") or [],
            "error": error,
            "started_at": previous.get("started_at"),
            "indexed_at": previous.get("indexed_at"),
        }

    # ------------------------------------------------------------------ reporting
    async def _write(self, base_id: str, record: dict[str, Any], event_type: str | None) -> None:
        def save() -> None:
            doc = _status_doc()
            doc["bases"][base_id] = dict(record)
            _save_status(doc)

        await asyncio.to_thread(save)
        if event_type:
            self._publish(event_type, {"base_id": base_id, **record})

    async def _finish(self, base_id: str, record: dict[str, Any]) -> None:
        previous = _status_doc()["bases"].get(base_id) or {}
        if (
            record["status"] == "failed"
            and previous.get("status") == "failed"
            and previous.get("error") == record.get("error")
        ):
            return  # unchanged failure (e.g. still unconfigured): no event storm
        event_type = (
            "knowledge.index.completed"
            if record["status"] == "indexed"
            else "knowledge.index.failed"
        )
        await self._write(base_id, record, event_type)
        try:
            await self._dispatch(event_type, {"key": base_id, **record})
        except Exception:
            logger.warning("Workflow dispatch for %s failed", event_type, exc_info=True)


_indexer: KnowledgeIndexer | None = None


def get_knowledge_indexer() -> KnowledgeIndexer:
    global _indexer
    if _indexer is None:
        _indexer = KnowledgeIndexer()
    return _indexer
