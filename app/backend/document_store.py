"""Backend access to Postgres-backed LangGraph documents."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, LiteralString, NamedTuple, Protocol, cast

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed

from app.backend.runtime_conversations import RuntimeConversationReader, RuntimeUnavailable
from app.progenitor.document_kinds import SESSION_KEY_PREFIX
from app.progenitor.routines.store import ROUTINE_GRAPH_KEY_PREFIX

PROGENITOR_STATE_PREFIX = "progenitor.state"
PROGENITOR_LOG_PREFIX = "progenitor.logs"
BACKEND_STATE_PREFIX = "backend.state"

# Postgres LISTEN/NOTIFY channel used to push ``store`` table changes to the
# backend without polling. Only state-namespace writes notify on it (the
# high-frequency ``progenitor.logs.*`` rows are deliberately excluded).
STORE_CHANGE_CHANNEL = "store_changed"
_STORE_CHANGE_PREFIXES = (PROGENITOR_STATE_PREFIX, BACKEND_STATE_PREFIX)
# Parallel runtime reads when the chat list needs messages for many threads.
CONVERSATION_FETCH_CONCURRENCY = 8

# Durable document event log: every create/update/delete of a state-namespace
# document appends one ``document_events`` row (with before/after payloads and
# the writer's origin) and NOTIFYs its sequence number on this channel.
DOCUMENT_EVENT_CHANNEL = "document_events"

DocumentEventOp = Literal["created", "updated", "deleted"]

# Session-scoped writer identity recorded on each document event. A workflow
# run sets it to its run id so events caused by its own writes carry their
# ancestry and cannot re-trigger the workflow that produced them.
_document_write_origin: ContextVar[str | None] = ContextVar(
    "progenitor_document_write_origin", default=None
)


@contextmanager
def document_write_origin(origin: str) -> Iterator[None]:
    """Tag every document write in this context with ``origin``."""

    token = _document_write_origin.set(origin)
    try:
        yield
    finally:
        _document_write_origin.reset(token)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredDocument:
    """One document loaded from the backend document store."""

    payload: Any
    updated_at: datetime | None = None


class DocumentEventPosition(NamedTuple):
    """A point in commit order: ``(txid, seq)`` of the last processed event.

    ``seq`` alone is allocated before commit, so a later-committing writer can
    hold a lower ``seq`` than events already visible. Ordering by the writing
    transaction id first, and only reading transactions older than every still
    open one, makes the visible prefix of the log final.
    """

    txid: int
    seq: int


EVENT_LOG_START = DocumentEventPosition(0, 0)


@dataclass(frozen=True)
class DocumentEvent:
    """One row of the durable document event log."""

    seq: int
    txid: int
    prefix: str
    key: str
    op: DocumentEventOp
    before: Any
    after: Any
    origin: str | None
    created_at: datetime

    @property
    def position(self) -> DocumentEventPosition:
        return DocumentEventPosition(self.txid, self.seq)


@dataclass(frozen=True)
class CheckpointThreadSnapshot:
    """Latest decoded LangGraph checkpoint messages for one thread."""

    thread_id: str
    checkpoint_id: str | None
    checkpoint_ts: str | None
    messages: list[Any]
    # Whether any checkpoint_writes row for this thread carries LangGraph's
    # own '__error__' channel -- see list_routine_runs for why this, not a
    # deserialized run_meta field, is the reliable failed/success signal.
    failed: bool = False
    # False when the runtime could not be reached to read ``messages`` (which is
    # then empty), so callers can tell "no messages" from "unknown right now".
    messages_available: bool = True


@dataclass(frozen=True)
class RoutineRunSummary:
    """One routine execution, reconstructed from its checkpoint thread.

    ``thread_id`` follows ``routine:<routine_id>:<revision_id>:<run_id>``
    (see ``app.progenitor.routines.subagent.create_routine_subagent``);
    ``revision_id``/``run_id`` are parsed out of it, not stored separately.
    """

    thread_id: str
    routine_id: str
    revision_id: str | None
    run_id: str | None
    started_at: str | None
    last_checkpoint_at: str | None
    checkpoint_count: int
    failed: bool


@dataclass(frozen=True)
class RoutineRevisionSummary:
    """One immutable snapshot of a routine's graph document.

    Rows come from ``routine_graph_revisions`` (see
    ``app.backend.document_store.PostgresDocumentStore._ensure_routine_revision_history``),
    which a Postgres trigger appends to on every write to a routine's live
    document -- newest-first here mirrors the trigger's own capture order.
    """

    revision_id: str
    routine_id: str
    captured_at: str | None


class BackendDocumentStore(Protocol):
    """Minimal document-store API used by backend routes."""

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None: ...

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]: ...

    def save_document(self, prefix: str, key: str, payload: Any) -> None: ...

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool: ...

    def delete_document(self, prefix: str, key: str) -> None: ...

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any: ...

    def read_document_events(
        self, after: DocumentEventPosition, limit: int
    ) -> list[DocumentEvent]: ...

    def latest_document_event_position(self) -> DocumentEventPosition: ...

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None: ...

    def save_document_event_cursor(
        self, consumer: str, position: DocumentEventPosition
    ) -> None: ...

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int: ...

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def get_log_sources(self) -> list[dict[str, Any]]: ...

    def inspect_schema(self) -> list[dict[str, Any]]: ...

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]: ...

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None: ...

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]: ...

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]: ...

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


class InMemoryDocumentStore:
    """Small test/local implementation with the same payload shape as Postgres."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], StoredDocument] = {}
        self._checkpoint_threads: dict[str, CheckpointThreadSnapshot] = {}
        self._routine_revisions: dict[
            tuple[str, str], tuple[RoutineRevisionSummary, dict[str, Any]]
        ] = {}
        self._events: list[DocumentEvent] = []
        # Allocation counter, independent of retained rows so pruning never
        # makes a later event reuse a processed sequence number.
        self._last_event_seq = 0
        self._event_cursors: dict[str, DocumentEventPosition] = {}
        self._event_listeners: list[Callable[[int], None]] = []
        self._lock = threading.Lock()

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        document = self._documents.get((prefix, key))
        if document is not None:
            return StoredDocument(copy.deepcopy(document.payload), document.updated_at)
        if default is None:
            return None
        return StoredDocument(default, None)

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]:
        return sorted(
            (
                (key, StoredDocument(copy.deepcopy(document.payload), document.updated_at))
                for (doc_prefix, key), document in self._documents.items()
                if doc_prefix == prefix and key.startswith(key_prefix)
            ),
            key=lambda entry: entry[0],
        )

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        # Copy like Postgres serialization does, so later in-place edits by the
        # caller cannot rewrite the stored value (or an event's before/after).
        stored = copy.deepcopy(payload)
        previous = self._documents.get((prefix, key))
        self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
        if previous is None:
            self._record_event(prefix, key, "created", None, stored)
        elif previous.payload != stored:
            self._record_event(prefix, key, "updated", previous.payload, stored)

    def delete_document(self, prefix: str, key: str) -> None:
        previous = self._documents.pop((prefix, key), None)
        if previous is not None:
            self._record_event(prefix, key, "deleted", previous.payload, None)

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        with self._lock:
            document = self._documents.get((prefix, key))
            payload = copy.deepcopy(document.payload) if document is not None else None
            new_payload = mutator(payload)
            stored = copy.deepcopy(new_payload)
            previous = self._documents.get((prefix, key))
            self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
            if previous is None:
                self._record_event(prefix, key, "created", None, stored)
            elif previous.payload != stored:
                self._record_event(prefix, key, "updated", previous.payload, stored)
            return new_payload

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool:
        previous = self._documents.get((prefix, key))
        if previous is None or not isinstance(previous.payload, dict):
            return False
        merged = {**previous.payload, **payload}
        stored = copy.deepcopy(merged)
        self._documents[(prefix, key)] = StoredDocument(stored, datetime.now(UTC))
        if previous.payload != stored:
            self._record_event(prefix, key, "updated", previous.payload, stored)
        return True

    def add_document_event_listener(self, listener: Callable[[int], None]) -> None:
        """Call ``listener(seq)`` after each recorded event (the in-memory NOTIFY)."""

        self._event_listeners.append(listener)

    def remove_document_event_listener(self, listener: Callable[[int], None]) -> None:
        if listener in self._event_listeners:
            self._event_listeners.remove(listener)

    def _record_event(
        self, prefix: str, key: str, op: DocumentEventOp, before: Any, after: Any
    ) -> None:
        if prefix not in _STORE_CHANGE_PREFIXES:
            return
        self._last_event_seq += 1
        seq = self._last_event_seq
        self._events.append(
            DocumentEvent(
                seq=seq,
                # Every in-memory write commits immediately, in allocation order.
                txid=seq,
                prefix=prefix,
                key=key,
                op=op,
                before=copy.deepcopy(before),
                after=copy.deepcopy(after),
                origin=_document_write_origin.get(),
                created_at=datetime.now(UTC),
            )
        )
        for listener in list(self._event_listeners):
            listener(seq)

    def read_document_events(self, after: DocumentEventPosition, limit: int) -> list[DocumentEvent]:
        return [event for event in self._events if event.position > after][:limit]

    def latest_document_event_position(self) -> DocumentEventPosition:
        return DocumentEventPosition(self._last_event_seq, self._last_event_seq)

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None:
        return self._event_cursors.get(consumer)

    def save_document_event_cursor(self, consumer: str, position: DocumentEventPosition) -> None:
        self._event_cursors[consumer] = position

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int:
        cutoff = datetime.now(UTC) - older_than
        kept = [
            event
            for event in self._events
            if event.position > through or event.created_at >= cutoff
        ]
        removed = len(self._events) - len(kept)
        self._events = kept
        return removed

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        source_prefix = f"{PROGENITOR_LOG_PREFIX}."
        for (prefix, key), document in self._documents.items():
            if not prefix.startswith(source_prefix):
                continue
            row = dict(document.payload) if isinstance(document.payload, dict) else {}
            row.setdefault("id", key)
            row.setdefault("source", prefix.removeprefix(source_prefix))
            if level and str(row.get("level", "")).lower() != level.lower():
                continue
            if source and str(row.get("source", "")) != source:
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("timestamp") or row.get("time") or ""))
        return rows[-limit:] if limit > 0 else rows

    def get_log_sources(self) -> list[dict[str, Any]]:
        sources = sorted(
            {
                prefix.removeprefix(f"{PROGENITOR_LOG_PREFIX}.")
                for prefix, _key in self._documents
                if prefix.startswith(f"{PROGENITOR_LOG_PREFIX}.")
            }
        )
        return [{"source": source, "count": 0} for source in sources]

    def inspect_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "table": "store",
                "columns": [
                    {"name": "prefix", "type": "TEXT", "nullable": False},
                    {"name": "key", "type": "TEXT", "nullable": False},
                    {"name": "value", "type": "JSONB", "nullable": False},
                    {"name": "created_at", "type": "TIMESTAMPTZ", "nullable": False},
                    {"name": "updated_at", "type": "TIMESTAMPTZ", "nullable": False},
                    {"name": "expires_at", "type": "TIMESTAMPTZ", "nullable": True},
                    {"name": "ttl_minutes", "type": "DOUBLE PRECISION", "nullable": True},
                ],
                "source": "in_memory",
            }
        ]

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]:
        snapshots = sorted(
            self._checkpoint_threads.values(),
            key=lambda snapshot: snapshot.checkpoint_ts or "",
            reverse=True,
        )
        return snapshots[:limit] if limit > 0 else snapshots

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None:
        snapshot = self._checkpoint_threads.get(thread_id)
        if snapshot is None:
            return None
        if checkpoint_id and snapshot.checkpoint_id != checkpoint_id:
            return None
        return snapshot

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]:
        prefix = f"routine:{routine_id}:"
        summaries = [
            RoutineRunSummary(
                thread_id=snapshot.thread_id,
                routine_id=routine_id,
                revision_id=(snapshot.thread_id.split(":", 3) + [None, None])[2],
                run_id=(snapshot.thread_id.split(":", 3) + [None, None, None])[3],
                started_at=snapshot.checkpoint_ts,
                last_checkpoint_at=snapshot.checkpoint_ts,
                checkpoint_count=1,
                failed=snapshot.failed,
            )
            for snapshot in self._checkpoint_threads.values()
            if snapshot.thread_id.startswith(prefix)
        ]
        summaries.sort(key=lambda summary: summary.last_checkpoint_at or "", reverse=True)
        if before:
            summaries = [s for s in summaries if (s.last_checkpoint_at or "") < before]
        if limit <= 0:
            return summaries, False
        page = summaries[:limit]
        return page, len(summaries) > limit

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]:
        summaries = [
            summary
            for (rid, _revision_id), (summary, _graph) in self._routine_revisions.items()
            if rid == routine_id
        ]
        summaries.sort(key=lambda summary: summary.captured_at or "", reverse=True)
        return summaries[:limit] if limit > 0 else summaries

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        entry = self._routine_revisions.get((routine_id, revision_id))
        return entry[1] if entry is not None else None

    def upsert_routine_revision(
        self,
        routine_id: str,
        revision_id: str,
        graph: dict[str, Any],
        *,
        captured_at: str | None = None,
    ) -> None:
        self._routine_revisions[(routine_id, revision_id)] = (
            RoutineRevisionSummary(
                revision_id=revision_id, routine_id=routine_id, captured_at=captured_at
            ),
            graph,
        )

    def upsert_checkpoint_thread_snapshot(self, snapshot: CheckpointThreadSnapshot) -> None:
        self._checkpoint_threads[snapshot.thread_id] = snapshot

    def close(self) -> None:
        return None


class PostgresDocumentStore:
    """Synchronous Postgres client for the LangGraph ``store`` table."""

    def __init__(self, database_url: str, *, runtime_url: str | None = None) -> None:
        self._database_url = database_url
        self._pool = self._create_pool()
        self._conversation_reader = RuntimeConversationReader(runtime_url) if runtime_url else None

    @property
    def database_url(self) -> str:
        return self._database_url

    def _create_pool(self) -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
        return ConnectionPool(
            conninfo=self._database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open()
        # Concurrent startups (backend workers, seed scripts) otherwise race on
        # the same CREATE OR REPLACE FUNCTION/TRIGGER catalog rows. A session
        # lock on one pooled connection serializes setup; the steps below use
        # other pooled connections.
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext('progenitor_document_store_setup'))")
            try:
                self._ensure_store_table()
                self._ensure_change_trigger()
                self._ensure_document_event_log()
                self._ensure_routine_revision_history()
                self._ensure_policy_revision_history()
            finally:
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtext('progenitor_document_store_setup'))"
                )

    def close(self) -> None:
        self._pool.close()
        if self._conversation_reader is not None:
            self._conversation_reader.close()

    def _recreate_pool(self) -> None:
        self._pool = self._create_pool()
        self._pool.open()

    def _checkpoint_messages(self, *, thread_id: str, checkpoint_id: str | None) -> list[Any]:
        """Messages of one thread checkpoint, read through the runtime.

        Raises ``RuntimeUnavailable`` when the runtime cannot be reached and
        no earlier copy of the thread is known.
        """
        if self._conversation_reader is None:
            return []
        return self._conversation_reader.get_messages(thread_id, checkpoint_id)

    @contextmanager
    def _cursor(self):
        """Yield a cursor, reopening the pool once if it was unexpectedly closed."""
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur
                return
        except PoolClosed:
            logger.warning("Backend document store pool was closed; recreating it")
            self._recreate_pool()
            self._ensure_store_table()
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur

    def _ensure_store_table(self) -> None:
        """Create the LangGraph-compatible store table when running in a fresh DB."""
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS store (
                    prefix TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ,
                    ttl_minutes DOUBLE PRECISION,
                    PRIMARY KEY (prefix, key)
                )
                """
            )

    def _ensure_change_trigger(self) -> None:
        """Install a NOTIFY trigger so state writes push to listeners without polling.

        Fires for both backend writes and the runtime's LangGraph store writes
        (same ``store`` table), including deletes (e.g. the agent removing a
        task from ``/config/schedule.json`` in the separate runtime process,
        which has no other way to tell the backend a row disappeared). Scoped
        to state-namespace prefixes so the noisy append-only
        ``progenitor.logs.*`` rows do not flood the channel. ``CREATE OR
        REPLACE`` + ``DROP ... IF EXISTS`` keep this idempotent.
        """
        prefixes = ", ".join(f"'{prefix}'" for prefix in _STORE_CHANGE_PREFIXES)
        with self._cursor() as cur:
            cur.execute(
                f"""
                CREATE OR REPLACE FUNCTION progenitor_notify_store_change()
                RETURNS trigger AS $$
                DECLARE
                    row_prefix TEXT := COALESCE(NEW.prefix, OLD.prefix);
                    row_key TEXT := COALESCE(NEW.key, OLD.key);
                    row_action TEXT := CASE WHEN TG_OP = 'DELETE' THEN 'deleted' ELSE 'upserted' END;
                BEGIN
                    IF row_prefix IN ({prefixes}) THEN
                        PERFORM pg_notify(
                            '{STORE_CHANGE_CHANNEL}',
                            json_build_object('prefix', row_prefix, 'key', row_key, 'action', row_action)::text
                        );
                    END IF;
                    RETURN COALESCE(NEW, OLD);
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progenitor_store_change
                AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_notify_store_change()
                """
            )

    def _ensure_document_event_log(self) -> None:
        """Install the durable document event log and its capture trigger.

        Every INSERT/UPDATE/DELETE of a state-namespace ``store`` row appends one
        ``document_events`` row carrying ``created``/``updated``/``deleted``, the
        before/after payloads, and the writer origin (``progenitor.origin``,
        set transaction-locally by ``document_write_origin``). Rewrites of an
        identical value are skipped. The row's sequence number is NOTIFYed on
        ``document_events`` so consumers wake up, then catch up from their
        stored cursor -- missed notifications never lose events. Like the
        revision histories, this lives in separate tables so LangGraph's own
        ``store`` schema is never altered.
        """
        prefixes = ", ".join(f"'{prefix}'" for prefix in _STORE_CHANGE_PREFIXES)
        with self._cursor() as cur, cur.connection.transaction():
            # ``txid`` is the writing transaction (commit-order key, see
            # ``DocumentEventPosition``). Added columns on existing tables are
            # filled in this one transaction, so every pre-existing event and
            # cursor shares one txid and keeps its relative ``seq`` order.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_events (
                    seq BIGSERIAL PRIMARY KEY,
                    txid XID8 NOT NULL DEFAULT pg_current_xact_id(),
                    prefix TEXT NOT NULL,
                    key TEXT NOT NULL,
                    op TEXT NOT NULL CHECK (op IN ('created', 'updated', 'deleted')),
                    before JSONB,
                    after JSONB,
                    origin TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE document_events ADD COLUMN IF NOT EXISTS "
                "txid XID8 NOT NULL DEFAULT pg_current_xact_id()"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS document_events_created_at_idx "
                "ON document_events (created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS document_events_position_idx "
                "ON document_events (txid, seq)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_event_cursors (
                    consumer TEXT PRIMARY KEY,
                    txid XID8 NOT NULL,
                    seq BIGINT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE document_event_cursors ADD COLUMN IF NOT EXISTS "
                "txid XID8 NOT NULL DEFAULT pg_current_xact_id()"
            )
            cur.execute("ALTER TABLE document_event_cursors ALTER COLUMN txid DROP DEFAULT")
            # Placeholders are filled from module constants only, never input.
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION progenitor_record_document_event()
                RETURNS trigger AS $$
                DECLARE
                    row_prefix TEXT := COALESCE(NEW.prefix, OLD.prefix);
                    event_seq BIGINT;
                BEGIN
                    IF row_prefix NOT IN (__PREFIXES__) THEN
                        RETURN NULL;
                    END IF;
                    IF TG_OP = 'UPDATE' AND OLD.value IS NOT DISTINCT FROM NEW.value THEN
                        RETURN NULL;
                    END IF;
                    -- Noise filter: content/last_activity-only updates on session docs
                    -- do not log an event, because they happen on every turn.
                    IF TG_OP = 'UPDATE'
                        AND row_prefix = 'progenitor.state'
                        AND COALESCE(NEW.key, OLD.key) LIKE '__SESSION_PREFIX__%'
                        AND (OLD.value -> 'payload') - 'content' - 'last_activity'
                            IS NOT DISTINCT FROM
                            (NEW.value -> 'payload') - 'content' - 'last_activity'
                    THEN
                        RETURN NULL;
                    END IF;
                    INSERT INTO document_events (prefix, key, op, before, after, origin)
                    VALUES (
                        row_prefix,
                        COALESCE(NEW.key, OLD.key),
                        CASE TG_OP WHEN 'INSERT' THEN 'created'
                                   WHEN 'UPDATE' THEN 'updated'
                                   ELSE 'deleted' END,
                        CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.value -> 'payload' END,
                        CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.value -> 'payload' END,
                        NULLIF(current_setting('progenitor.origin', true), '')
                    )
                    RETURNING seq INTO event_seq;
                    PERFORM pg_notify('__CHANNEL__', event_seq::text);
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """.replace("__PREFIXES__", prefixes)  # nosec B608
                .replace("__CHANNEL__", DOCUMENT_EVENT_CHANNEL)
                .replace("__SESSION_PREFIX__", SESSION_KEY_PREFIX)
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progenitor_document_event
                AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_record_document_event()
                """
            )

    def _ensure_routine_revision_history(self) -> None:
        """Capture immutable routine graph revisions via a Postgres trigger.

        The ``store`` table is LangGraph's own; this never alters its schema.
        A row trigger on ``store`` inserts into a separate
        ``routine_graph_revisions`` table whenever a routine document is
        written, so a chat thread that pinned a revision keeps resolving the
        graph it started on even after the routine is edited or deleted.
        DELETE captures OLD in the same transaction, preserving the final live
        snapshot even when it predates installation of the capture trigger.
        """
        state_prefix = PROGENITOR_STATE_PREFIX
        key_prefix = ROUTINE_GRAPH_KEY_PREFIX
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS routine_graph_revisions (
                    routine_id  TEXT NOT NULL,
                    graph       JSONB NOT NULL,
                    revision_id TEXT NOT NULL GENERATED ALWAYS AS (encode(sha256(jsonb_send(graph)), 'hex')) STORED,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (routine_id, revision_id)
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS routine_graph_revisions_latest
                    ON routine_graph_revisions (routine_id, captured_at DESC, revision_id DESC)
                """
            )
            capture_sql = """
                CREATE OR REPLACE FUNCTION progenitor_capture_routine_revision()
                RETURNS trigger AS $$
                DECLARE
                    graph_doc JSONB;
                    source_row store%ROWTYPE;
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        source_row := OLD;
                    ELSE
                        source_row := NEW;
                    END IF;
                    IF source_row.prefix = '__STATE_PREFIX__'
                        AND source_row.key LIKE '__KEY_PREFIX__%'
                        AND length(source_row.key) > length('__KEY_PREFIX__') THEN
                        graph_doc := source_row.value -> 'payload';
                        IF graph_doc IS NOT NULL AND jsonb_typeof(graph_doc) = 'object' THEN
                            INSERT INTO routine_graph_revisions (routine_id, graph)
                            VALUES (substring(source_row.key from length('__KEY_PREFIX__') + 1), graph_doc)
                            ON CONFLICT (routine_id, revision_id) DO NOTHING;
                        END IF;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            cur.execute(capture_sql)
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progenitor_capture_routine_revision
                AFTER INSERT OR UPDATE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_capture_routine_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progenitor_capture_deleted_routine_revision
                BEFORE DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_capture_routine_revision()
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE FUNCTION progenitor_routine_revisions_immutable()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'routine_graph_revisions rows are immutable (attempted % on %/%)',
                        TG_OP, OLD.routine_id, OLD.revision_id;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute(
                """
                CREATE OR REPLACE TRIGGER progenitor_routine_revisions_immutable
                BEFORE UPDATE ON routine_graph_revisions
                FOR EACH ROW EXECUTE FUNCTION progenitor_routine_revisions_immutable()
                """
            )
            backfill_sql = """
                INSERT INTO routine_graph_revisions (routine_id, graph)
                SELECT substring(key from length('__KEY_PREFIX__') + 1), value -> 'payload'
                FROM store
                WHERE prefix = '__STATE_PREFIX__'
                  AND key LIKE '__KEY_PREFIX__%'
                  AND length(key) > length('__KEY_PREFIX__')
                  AND jsonb_typeof(value -> 'payload') = 'object'
                ON CONFLICT (routine_id, revision_id) DO NOTHING
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)  # nosec B608
            cur.execute(backfill_sql)

    def _ensure_policy_revision_history(self) -> None:
        from app.progenitor.policies import ExecutionPolicy

        with self._cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS policy_revisions (
                    policy_id TEXT NOT NULL, document JSONB NOT NULL,
                    revision_id TEXT GENERATED ALWAYS AS (encode(sha256(jsonb_send(document)), 'hex')) STORED,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (policy_id, revision_id)
                );
                CREATE OR REPLACE FUNCTION progenitor_capture_policy_revision() RETURNS trigger AS $$
                DECLARE source_row store%ROWTYPE;
                BEGIN
                    IF TG_OP = 'DELETE' THEN source_row := OLD; ELSE source_row := NEW; END IF;
                    IF source_row.prefix = 'progenitor.state' AND source_row.key LIKE 'policy:%'
                        AND jsonb_typeof(source_row.value->'payload') = 'object' THEN
                        INSERT INTO policy_revisions(policy_id, document)
                        VALUES (substring(source_row.key from 8), source_row.value->'payload')
                        ON CONFLICT DO NOTHING;
                    END IF;
                    RETURN source_row;
                END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progenitor_capture_policy_revision AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_capture_policy_revision();
                CREATE OR REPLACE FUNCTION progenitor_policy_revisions_immutable() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'policy revisions are immutable'; END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE TRIGGER progenitor_policy_revisions_immutable BEFORE UPDATE OR DELETE ON policy_revisions
                FOR EACH ROW EXECUTE FUNCTION progenitor_policy_revisions_immutable();
            """)
            cur.execute(
                """INSERT INTO store(prefix,key,value,created_at,updated_at)
                VALUES (%s,%s,%s,now(),now()) ON CONFLICT(prefix,key) DO NOTHING""",
                (
                    PROGENITOR_STATE_PREFIX,
                    "policy:default",
                    Jsonb(
                        {
                            "payload": ExecutionPolicy(id="default", name="Default").model_dump(
                                mode="json"
                            )
                        }
                    ),
                ),
            )

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT value, updated_at FROM store WHERE prefix = %s AND key = %s",
                (prefix, key),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            if default is None:
                return None
            return StoredDocument(default, None)
        value = row.get("value")
        payload = value.get("payload", default) if isinstance(value, dict) else default
        return StoredDocument(payload, row.get("updated_at"))

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT key, value, updated_at FROM store "
                "WHERE prefix = %s AND key LIKE %s ORDER BY key",
                (prefix, _escape_like(key_prefix) + "%"),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        results: list[tuple[str, StoredDocument]] = []
        for row in rows:
            value = row.get("value")
            payload = value.get("payload") if isinstance(value, dict) else None
            results.append((str(row["key"]), StoredDocument(payload, row.get("updated_at"))))
        return results

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        value = {"payload": payload}
        self._write_with_origin(
            """
            INSERT INTO store (prefix, key, value, created_at, updated_at)
            VALUES (%s, %s, %s, now(), now())
            ON CONFLICT (prefix, key) DO UPDATE
                SET value = EXCLUDED.value,
                    updated_at = now()
            """,
            (prefix, key, Jsonb(value)),
        )

    def merge_document(self, prefix: str, key: str, payload: Any) -> bool:
        """Merge ``payload`` into an existing document's payload; ``False`` if it is missing."""

        return (
            self._write_with_origin(
                """
            UPDATE store
            SET value = jsonb_set(value, '{payload}', (value->'payload') || %s::jsonb),
                updated_at = now()
            WHERE prefix = %s AND key = %s
            """,
                (Jsonb(payload), prefix, key),
            )
            > 0
        )

    def delete_document(self, prefix: str, key: str) -> None:
        self._write_with_origin(
            "DELETE FROM store WHERE prefix = %s AND key = %s",
            (prefix, key),
        )

    def mutate_document(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        try:
            return self._mutate_document_impl(prefix, key, mutator)
        except PoolClosed:
            logger.warning("Backend document store pool was closed; recreating it")
            self._recreate_pool()
            self._ensure_store_table()
            return self._mutate_document_impl(prefix, key, mutator)

    def _mutate_document_impl(self, prefix: str, key: str, mutator: Callable[[Any], Any]) -> Any:
        origin = _document_write_origin.get()
        with self._pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # A transaction-scoped lock on the key (not a row lock), so
                # two first writes of a missing document serialize too.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                    (prefix, key),
                )
                cur.execute(
                    "SELECT value FROM store WHERE prefix = %s AND key = %s",
                    (prefix, key),
                )
                row = cast(dict[str, Any] | None, cur.fetchone())
                payload = row["value"]["payload"] if row is not None else None
                new_payload = mutator(payload)
                value = {"payload": new_payload}
                if origin is not None:
                    cur.execute("SELECT set_config('progenitor.origin', %s, true)", (origin,))
                cur.execute(
                    """
                        INSERT INTO store (prefix, key, value, created_at, updated_at)
                        VALUES (%s, %s, %s, now(), now())
                        ON CONFLICT (prefix, key) DO UPDATE
                            SET value = EXCLUDED.value,
                                updated_at = now()
                        """,
                    (prefix, key, Jsonb(value)),
                )
            return new_payload

    def _write_with_origin(self, query: LiteralString, params: tuple[Any, ...]) -> int:
        """Run one store write, tagging it with the context's writer origin.

        ``set_config(..., true)`` is transaction-local, so the tagged write runs
        in an explicit transaction where the event-log trigger can read it.
        """
        origin = _document_write_origin.get()
        with self._cursor() as cur:
            if origin is None:
                cur.execute(query, params)
                return cur.rowcount
            with cur.connection.transaction():
                cur.execute("SELECT set_config('progenitor.origin', %s, true)", (origin,))
                cur.execute(query, params)
            return cur.rowcount

    def read_document_events(self, after: DocumentEventPosition, limit: int) -> list[DocumentEvent]:
        """Events after ``after`` from transactions older than every open one.

        Every transaction below the snapshot xmin has committed or rolled back,
        so this prefix of the ``(txid, seq)`` order can never gain a new row.
        """

        with self._cursor() as cur:
            cur.execute(
                """
                SELECT seq, txid::text::bigint AS txid, prefix, key, op, before, after,
                       origin, created_at
                FROM document_events
                WHERE txid < pg_snapshot_xmin(pg_current_snapshot())
                  AND (txid, seq) > (%s::text::xid8, %s)
                ORDER BY txid, seq
                LIMIT %s
                """,
                (str(after.txid), after.seq, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            DocumentEvent(
                seq=int(row["seq"]),
                txid=int(row["txid"]),
                prefix=str(row["prefix"]),
                key=str(row["key"]),
                op=cast(DocumentEventOp, row["op"]),
                before=row.get("before"),
                after=row.get("after"),
                origin=row.get("origin"),
                created_at=row["created_at"].astimezone(UTC),
            )
            for row in rows
        ]

    def latest_document_event_position(self) -> DocumentEventPosition:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT txid::text::bigint AS txid, seq
                FROM document_events
                WHERE txid < pg_snapshot_xmin(pg_current_snapshot())
                ORDER BY txid DESC, seq DESC
                LIMIT 1
                """
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return EVENT_LOG_START
        return DocumentEventPosition(int(row["txid"]), int(row["seq"]))

    def get_document_event_cursor(self, consumer: str) -> DocumentEventPosition | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT txid::text::bigint AS txid, seq FROM document_event_cursors "
                "WHERE consumer = %s",
                (consumer,),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        return DocumentEventPosition(int(row["txid"]), int(row["seq"]))

    def save_document_event_cursor(self, consumer: str, position: DocumentEventPosition) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_event_cursors (consumer, txid, seq, updated_at)
                VALUES (%s, %s::text::xid8, %s, now())
                ON CONFLICT (consumer) DO UPDATE
                    SET txid = EXCLUDED.txid, seq = EXCLUDED.seq, updated_at = now()
                """,
                (consumer, str(position.txid), position.seq),
            )

    def prune_document_events(
        self, *, through: DocumentEventPosition, older_than: timedelta
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM document_events "
                "WHERE (txid, seq) <= (%s::text::xid8, %s) AND created_at < now() - %s",
                (str(through.txid), through.seq, older_than),
            )
            return cur.rowcount

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = [sql.SQL("prefix LIKE %s")]
        params: list[Any] = [f"{PROGENITOR_LOG_PREFIX}.%"]
        if source:
            clauses.append(sql.SQL("prefix = %s"))
            params.append(f"{PROGENITOR_LOG_PREFIX}.{source}")
        if level:
            clauses.append(sql.SQL("lower(value->>'level') = %s"))
            params.append(level.lower())
        if limit > 0:
            params.append(limit)
        where_clause = sql.SQL(" AND ").join(clauses)
        limit_clause = sql.SQL("LIMIT %s") if limit > 0 else sql.SQL("")
        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                SELECT prefix, key, value, updated_at
                FROM store
                WHERE {}
                ORDER BY COALESCE(value->>'timestamp', value->>'time', updated_at::text) DESC
                {}
                """
                ).format(where_clause, limit_clause),
                params,
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [_log_row_to_entry(row) for row in reversed(rows)]

    def get_log_sources(self) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT
                    replace(prefix, %s, '') AS source,
                    count(*) AS count,
                    max(updated_at) AS updated_at
                FROM store
                WHERE prefix LIKE %s
                GROUP BY prefix
                ORDER BY source
                """,
                (f"{PROGENITOR_LOG_PREFIX}.", f"{PROGENITOR_LOG_PREFIX}.%"),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [dict(row) for row in rows]

    def inspect_schema(self) -> list[dict[str, Any]]:
        """Return table/column metadata for backend and LangGraph persistence tables."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND (
                    table_name = 'store'
                    OR table_name LIKE 'checkpoint%'
                    OR table_name LIKE 'langgraph%'
                  )
                ORDER BY table_name, ordinal_position
                """
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())

        tables: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            table_name = str(row["table_name"])
            tables.setdefault(table_name, []).append(
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                }
            )
        return [
            {"table": table_name, "columns": columns, "source": "postgres"}
            for table_name, columns in tables.items()
        ]

    def list_checkpoint_thread_snapshots(
        self, *, limit: int = 50
    ) -> list[CheckpointThreadSnapshot]:
        """Return latest reconstructed message snapshots for LangGraph threads."""
        with self._cursor() as cur:
            cur.execute(
                """
                WITH ranked AS (
                    SELECT
                        c.thread_id,
                        c.checkpoint_ns,
                        c.checkpoint_id,
                        c.checkpoint->>'ts' AS checkpoint_ts,
                        row_number() OVER (
                            PARTITION BY c.thread_id
                            ORDER BY (c.checkpoint->>'ts')::timestamptz DESC, c.checkpoint_id DESC
                        ) AS rn
                    FROM checkpoints c
                    -- Filters on channel_versions, not channel_values.
                    WHERE c.checkpoint_ns = ''
                      AND c.checkpoint ? 'channel_versions'
                      AND c.checkpoint->'channel_versions' ? 'messages'
                )
                SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_ts
                FROM ranked
                WHERE rn = 1
                ORDER BY checkpoint_ts DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        targets = [
            (
                str(row["thread_id"]),
                str(row["checkpoint_id"]) if row.get("checkpoint_id") else None,
                row,
            )
            for row in rows
            if isinstance(row.get("thread_id"), str) and row.get("thread_id")
        ]

        def _messages_or_unavailable(target: tuple[str, str | None, Any]) -> list[Any] | None:
            # The list must still render while the runtime is down; None marks
            # the thread's messages as unknown rather than empty.
            thread_id, checkpoint_id, _row = target
            try:
                return self._checkpoint_messages(thread_id=thread_id, checkpoint_id=checkpoint_id)
            except RuntimeUnavailable:
                return None

        with ThreadPoolExecutor(max_workers=CONVERSATION_FETCH_CONCURRENCY) as pool:
            all_messages = list(pool.map(_messages_or_unavailable, targets))
        return [
            CheckpointThreadSnapshot(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
                messages=messages or [],
                messages_available=messages is not None,
            )
            for (thread_id, checkpoint_id, row), messages in zip(targets, all_messages, strict=True)
        ]

    def get_checkpoint_thread_snapshot(
        self,
        thread_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> CheckpointThreadSnapshot | None:
        # Same channel_versions-vs-channel_values caveat as
        # list_checkpoint_thread_snapshots() above.
        clauses = [
            sql.SQL("c.thread_id = %s"),
            sql.SQL("c.checkpoint_ns = ''"),
            sql.SQL("c.checkpoint ? 'channel_versions'"),
            sql.SQL("c.checkpoint->'channel_versions' ? 'messages'"),
        ]
        params: list[Any] = [thread_id]
        if checkpoint_id:
            clauses.append(sql.SQL("c.checkpoint_id = %s"))
            params.append(checkpoint_id)
        where_clause = sql.SQL(" AND ").join(clauses)
        with self._cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                SELECT c.thread_id, c.checkpoint_id, c.checkpoint->>'ts' AS checkpoint_ts
                     , c.checkpoint_ns
                FROM checkpoints c
                WHERE {}
                ORDER BY (c.checkpoint->>'ts')::timestamptz DESC, c.checkpoint_id DESC
                LIMIT 1
                """
                ).format(where_clause),
                params,
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        resolved_thread_id = row.get("thread_id")
        if not isinstance(resolved_thread_id, str) or not resolved_thread_id:
            return None
        resolved_checkpoint_id = str(row["checkpoint_id"]) if row.get("checkpoint_id") else None
        messages = self._checkpoint_messages(
            thread_id=resolved_thread_id, checkpoint_id=resolved_checkpoint_id
        )
        return CheckpointThreadSnapshot(
            thread_id=resolved_thread_id,
            checkpoint_id=resolved_checkpoint_id,
            checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
            messages=messages,
        )

    def list_routine_runs(
        self, routine_id: str, *, limit: int = 50, before: str | None = None
    ) -> tuple[list[RoutineRunSummary], bool]:
        """Every distinct routine-run checkpoint thread for one routine.

        A run's thread_id is ``routine:<routine_id>:<revision_id>:<run_id>``
        (see ``app.progenitor.routines.subagent.create_routine_subagent``).
        Groups raw checkpoint rows by thread_id -- one row per run, newest
        last-checkpoint first -- rather than reconstructing full message
        history, which the Runs panel doesn't need.

        ``before`` is a keyset cursor: the ``last_checkpoint_at`` of the
        oldest run already loaded by the caller (verbatim string this same
        method previously returned), so paging never relies on OFFSET
        against a live-growing table. Fetches one extra row over ``limit``
        purely to answer ``has_more`` without a second COUNT query, then
        trims it back off before returning.

        ``failed`` is a per-run ``EXISTS`` against ``checkpoint_writes`` for
        LangGraph's own ``__error__`` pending-write channel -- written
        unconditionally by LangGraph's task runner the instant any node
        raises, *before* any of our own error-handling code runs. This is
        deliberately NOT derived from the ``run_meta`` channel's own
        per-node ``error`` field: that field only exists when
        subagent.py's narrow exception catch fires AND its own backfill
        write succeeds (both best-effort), so it under-reports real
        failures. The EXISTS runs once per already-LIMIT-bounded row,
        against ``checkpoint_writes_thread_id_idx`` -- no checkpoint blob
        deserialization, no extra connection, one round trip alongside the
        existing GROUP BY.
        """
        escaped = routine_id.translate({ord("\\"): "\\\\", ord("%"): "\\%", ord("_"): "\\_"})
        pattern = f"routine:{escaped}:%"
        params: list[Any] = [pattern, before is None, before, limit + 1]
        with self._cursor() as cur:
            cur.execute(
                """
                WITH runs AS (
                    SELECT
                        thread_id,
                        min((checkpoint->>'ts')::timestamptz) AS started_at,
                        max((checkpoint->>'ts')::timestamptz) AS last_checkpoint_at,
                        count(*) AS checkpoint_count
                    FROM checkpoints
                    WHERE thread_id LIKE %s ESCAPE '\\'
                    GROUP BY thread_id
                    HAVING %s OR max((checkpoint->>'ts')::timestamptz) < %s::timestamptz
                    ORDER BY last_checkpoint_at DESC
                    LIMIT %s
                )
                SELECT
                    runs.*,
                    EXISTS (
                        SELECT 1 FROM checkpoint_writes cw
                        WHERE cw.thread_id = runs.thread_id AND cw.channel = '__error__'
                    ) AS failed
                FROM runs
                ORDER BY last_checkpoint_at DESC
                """,
                params,
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        has_more = len(rows) > limit
        rows = rows[:limit]
        summaries: list[RoutineRunSummary] = []
        for row in rows:
            thread_id = row.get("thread_id")
            if not isinstance(thread_id, str):
                continue
            parts = thread_id.split(":", 3)
            revision_id = parts[2] if len(parts) > 2 else None
            run_id = parts[3] if len(parts) > 3 else None
            summaries.append(
                RoutineRunSummary(
                    thread_id=thread_id,
                    routine_id=routine_id,
                    revision_id=revision_id,
                    run_id=run_id,
                    started_at=str(row["started_at"]) if row.get("started_at") else None,
                    last_checkpoint_at=(
                        str(row["last_checkpoint_at"]) if row.get("last_checkpoint_at") else None
                    ),
                    checkpoint_count=int(row["checkpoint_count"] or 0),
                    failed=bool(row.get("failed")),
                )
            )
        return summaries, has_more

    def list_routine_revisions(
        self, routine_id: str, *, limit: int = 50
    ) -> list[RoutineRevisionSummary]:
        """Every captured revision of one routine's graph document, newest first.

        Mirrors ``app.progenitor.routines.revisions.PostgresRoutineRevisionReader``,
        reading the same ``routine_graph_revisions`` table this store's own
        ``_ensure_routine_revision_history`` DDL/trigger populates.
        """
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT revision_id, captured_at
                FROM routine_graph_revisions
                WHERE routine_id = %s
                ORDER BY captured_at DESC, revision_id DESC
                LIMIT %s
                """,
                (routine_id, limit),
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [
            RoutineRevisionSummary(
                revision_id=row["revision_id"],
                routine_id=routine_id,
                captured_at=str(row["captured_at"]) if row.get("captured_at") else None,
            )
            for row in rows
        ]

    def get_routine_revision(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT graph
                FROM routine_graph_revisions
                WHERE routine_id = %s AND revision_id = %s
                """,
                (routine_id, revision_id),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return cast(dict[str, Any], row["graph"]) if row is not None else None


def store_database_url(store: BackendDocumentStore | None) -> str | None:
    """Return the Postgres DSN backing ``store``, or None for non-Postgres stores."""
    url = getattr(store, "database_url", None)
    return url if isinstance(url, str) else None


class StoreChangeListener:
    """Listen for ``store`` table changes over Postgres LISTEN/NOTIFY.

    Fully event-driven: on every (re)connect it fires ``on_resync`` so callers
    can recover any changes missed while disconnected, then streams NOTIFY
    events to ``on_change``. There is no polling timer.
    """

    def __init__(
        self,
        database_url: str,
        *,
        on_change: Callable[[str], None],
        on_resync: Callable[[], None] | None = None,
        reconnect_delay: float = 2.0,
        channel: str = STORE_CHANGE_CHANNEL,
    ) -> None:
        self._database_url = database_url
        self._on_change = on_change
        self._on_resync = on_resync
        self._reconnect_delay = reconnect_delay
        self._channel = channel
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
                    "Store change listener disconnected (%s); reconnecting in %ss",
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
            # Subscribe first so changes during resync are queued, not lost.
            await aconn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(self._channel)))
            if self._on_resync is not None:
                self._on_resync()
            logger.info("Listening for store changes on %r", self._channel)
            async for notify in aconn.notifies():
                self._on_change(notify.payload)
        finally:
            await aconn.close()


def _escape_like(text: str) -> str:
    """Escape LIKE wildcards so a key prefix matches literally (ids may contain ``_``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _log_row_to_entry(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("value")
    entry = dict(value) if isinstance(value, dict) else {}
    source = str(row.get("prefix") or "").removeprefix(f"{PROGENITOR_LOG_PREFIX}.")
    entry.setdefault("id", row.get("key"))
    entry.setdefault("source", source)
    entry.setdefault("timestamp", _timestamp_text(entry.get("timestamp") or row.get("updated_at")))
    entry.setdefault("time", entry["timestamp"])
    entry.setdefault("level", "info")
    entry.setdefault("message", "")
    entry.setdefault("relative_path", f"{entry['source']}.jsonl")
    return entry


def _timestamp_text(value: Any) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


_default_store: BackendDocumentStore = InMemoryDocumentStore()


def configure_default_document_store(
    database_url: str | None, *, runtime_url: str | None = None
) -> BackendDocumentStore:
    """Configure the process-wide backend document store."""
    global _default_store
    _default_store.close()
    if database_url:
        pg_store = PostgresDocumentStore(database_url, runtime_url=runtime_url)
        pg_store.open()
        store: BackendDocumentStore = pg_store
        logger.info("Backend document store connected to Postgres")
    else:
        store = InMemoryDocumentStore()
        logger.warning("PROGENITOR_DATABASE_URL is not set; backend document store is in-memory")
    _default_store = store
    return _default_store


def set_default_document_store(store: BackendDocumentStore) -> None:
    """Install a store explicitly, primarily for tests."""
    global _default_store
    _default_store.close()
    _default_store = store


def get_default_document_store() -> BackendDocumentStore:
    return _default_store


def seed_documents(
    documents: Iterable[tuple[str, str, Any]],
    *,
    store: BackendDocumentStore | None = None,
) -> BackendDocumentStore:
    target = store or _default_store
    for prefix, key, payload in documents:
        target.save_document(prefix, key, payload)
    return target
