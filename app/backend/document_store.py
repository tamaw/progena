"""Backend access to Postgres-backed LangGraph documents."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed

from app.backend.delta_channel_replay import replay_delta_messages
from app.progenitor.routines.store import ROUTINE_GRAPH_KEY_PREFIX

PROGENITOR_STATE_PREFIX = "progenitor.state"
PROGENITOR_LOG_PREFIX = "progenitor.logs"
BACKEND_STATE_PREFIX = "backend.state"

# Postgres LISTEN/NOTIFY channel used to push ``store`` table changes to the
# backend without polling. Only state-namespace writes notify on it (the
# high-frequency ``progenitor.logs.*`` rows are deliberately excluded).
STORE_CHANGE_CHANNEL = "store_changed"
_STORE_CHANGE_PREFIXES = (PROGENITOR_STATE_PREFIX, BACKEND_STATE_PREFIX)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredDocument:
    """One document loaded from the backend document store."""

    payload: Any
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CheckpointThreadSnapshot:
    """Latest decoded LangGraph checkpoint messages for one thread."""

    thread_id: str
    checkpoint_id: str | None
    checkpoint_ts: str | None
    messages: list[Any]


_MESSAGES_CHANNEL = "messages"


def _reconstruct_checkpoint_messages(
    saver: PostgresSaver,
    *,
    thread_id: str,
    checkpoint_ns: str = "",
    checkpoint_id: str | None,
) -> list[Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    config: RunnableConfig = {"configurable": configurable}
    checkpoint = saver.get_tuple(config)
    if checkpoint is None:
        return []
    return _messages_from_checkpoint_tuple(saver, checkpoint)


def _messages_from_checkpoint_tuple(saver: PostgresSaver, checkpoint: CheckpointTuple) -> list[Any]:
    channel_values = checkpoint.checkpoint.get("channel_values", {})
    if isinstance(channel_values, dict) and _MESSAGES_CHANNEL in channel_values:
        messages = channel_values[_MESSAGES_CHANNEL]
        return list(messages) if isinstance(messages, list) else []

    # `messages` is a DeltaChannel (deepagents>=0.6): checkpoints only carry a
    # value in channel_values every snapshot_frequency turns, so its absence
    # here is normal. Replay the deltas from checkpoint_writes the same way
    # LangGraph's own channels_from_checkpoint() does -- see
    # delta_channel_replay.py for the replay itself and its caveats.
    history = saver.get_delta_channel_history(
        config=checkpoint.config, channels=[_MESSAGES_CHANNEL]
    )
    entry = history.get(_MESSAGES_CHANNEL)
    if entry is None:
        return []
    return replay_delta_messages(entry)


class BackendDocumentStore(Protocol):
    """Minimal document-store API used by backend routes."""

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None: ...

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]: ...

    def save_document(self, prefix: str, key: str, payload: Any) -> None: ...

    def delete_document(self, prefix: str, key: str) -> None: ...

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

    def close(self) -> None: ...


class InMemoryDocumentStore:
    """Small test/local implementation with the same payload shape as Postgres."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], StoredDocument] = {}
        self._checkpoint_threads: dict[str, CheckpointThreadSnapshot] = {}

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        document = self._documents.get((prefix, key))
        if document is not None:
            return document
        if default is None:
            return None
        return StoredDocument(default, None)

    def list_documents(self, prefix: str, key_prefix: str) -> list[tuple[str, StoredDocument]]:
        return sorted(
            (
                (key, document)
                for (doc_prefix, key), document in self._documents.items()
                if doc_prefix == prefix and key.startswith(key_prefix)
            ),
            key=lambda entry: entry[0],
        )

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        self._documents[(prefix, key)] = StoredDocument(payload, datetime.now(UTC))

    def delete_document(self, prefix: str, key: str) -> None:
        self._documents.pop((prefix, key), None)

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

    def upsert_checkpoint_thread_snapshot(self, snapshot: CheckpointThreadSnapshot) -> None:
        self._checkpoint_threads[snapshot.thread_id] = snapshot

    def close(self) -> None:
        return None


class PostgresDocumentStore:
    """Synchronous Postgres client for the LangGraph ``store`` table."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool = self._create_pool()

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
        self._ensure_store_table()
        self._ensure_change_trigger()
        self._ensure_routine_revision_history()

    def close(self) -> None:
        self._pool.close()

    def _recreate_pool(self) -> None:
        self._pool = self._create_pool()
        self._pool.open()

    def _checkpoint_saver(self) -> PostgresSaver:
        return PostgresSaver(self._pool)

    def _checkpoint_messages(
        self,
        *,
        thread_id: str,
        checkpoint_ns: str = "",
        checkpoint_id: str | None,
    ) -> list[Any]:
        try:
            return _reconstruct_checkpoint_messages(
                self._checkpoint_saver(),
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
        except PoolClosed:
            logger.warning("Backend checkpoint saver pool was closed; recreating it")
            self._recreate_pool()
            self._ensure_store_table()
            return _reconstruct_checkpoint_messages(
                self._checkpoint_saver(),
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )

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
                BEGIN
                    IF row_prefix IN ({prefixes}) THEN
                        PERFORM pg_notify(
                            '{STORE_CHANGE_CHANNEL}',
                            json_build_object('prefix', row_prefix, 'key', row_key)::text
                        );
                    END IF;
                    RETURN COALESCE(NEW, OLD);
                END;
                $$ LANGUAGE plpgsql;
                """
            )
            cur.execute("DROP TRIGGER IF EXISTS progenitor_store_change ON store")
            cur.execute(
                """
                CREATE TRIGGER progenitor_store_change
                AFTER INSERT OR UPDATE OR DELETE ON store
                FOR EACH ROW EXECUTE FUNCTION progenitor_notify_store_change()
                """
            )

    def _ensure_routine_revision_history(self) -> None:
        """Capture immutable routine graph revisions via a Postgres trigger.

        The ``store`` table is LangGraph's own; this never alters its schema.
        A row trigger on ``store`` inserts into a separate
        ``routine_graph_revisions`` table whenever a routine document is
        written, so a chat thread that pinned a revision keeps resolving the
        graph it started on even after the routine is edited again.
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
            capture_sql = (  # nosec B608 - fixed internal namespace placeholders
                """
                CREATE OR REPLACE FUNCTION progenitor_capture_routine_revision()
                RETURNS trigger AS $$
                DECLARE
                    graph_doc JSONB;
                BEGIN
                    IF NEW.prefix = '__STATE_PREFIX__'
                        AND NEW.key LIKE '__KEY_PREFIX__%'
                        AND length(NEW.key) > length('__KEY_PREFIX__') THEN
                        graph_doc := NEW.value -> 'payload';
                        IF graph_doc IS NOT NULL AND jsonb_typeof(graph_doc) = 'object' THEN
                            INSERT INTO routine_graph_revisions (routine_id, graph)
                            VALUES (substring(NEW.key from length('__KEY_PREFIX__') + 1), graph_doc)
                            ON CONFLICT (routine_id, revision_id) DO NOTHING;
                        END IF;
                    END IF;
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)
            )
            cur.execute(capture_sql)
            cur.execute("DROP TRIGGER IF EXISTS progenitor_capture_routine_revision ON store")
            cur.execute(
                """
                CREATE TRIGGER progenitor_capture_routine_revision
                AFTER INSERT OR UPDATE ON store
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
                "DROP TRIGGER IF EXISTS progenitor_routine_revisions_immutable ON routine_graph_revisions"
            )
            cur.execute(
                """
                CREATE TRIGGER progenitor_routine_revisions_immutable
                BEFORE UPDATE ON routine_graph_revisions
                FOR EACH ROW EXECUTE FUNCTION progenitor_routine_revisions_immutable()
                """
            )
            backfill_sql = (  # nosec B608 - fixed internal namespace placeholders
                """
                INSERT INTO routine_graph_revisions (routine_id, graph)
                SELECT substring(key from length('__KEY_PREFIX__') + 1), value -> 'payload'
                FROM store
                WHERE prefix = '__STATE_PREFIX__'
                  AND key LIKE '__KEY_PREFIX__%'
                  AND length(key) > length('__KEY_PREFIX__')
                  AND jsonb_typeof(value -> 'payload') = 'object'
                ON CONFLICT (routine_id, revision_id) DO NOTHING
                """.replace("__STATE_PREFIX__", state_prefix).replace("__KEY_PREFIX__", key_prefix)
            )
            cur.execute(backfill_sql)

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
        with self._cursor() as cur:
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

    def delete_document(self, prefix: str, key: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM store WHERE prefix = %s AND key = %s",
                (prefix, key),
            )

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
                    -- Filters on channel_versions, not channel_values: the
                    -- messages DeltaChannel bumps a version on every write
                    -- but only lands in channel_values on a snapshot turn.
                    -- _messages_from_checkpoint_tuple() (above) is what
                    -- actually reconstructs the value for non-snapshot rows.
                    WHERE c.checkpoint ? 'channel_versions'
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
        snapshots: list[CheckpointThreadSnapshot] = []
        for row in rows:
            thread_id = row.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                continue
            checkpoint_ns = row.get("checkpoint_ns")
            resolved_checkpoint_ns = checkpoint_ns if isinstance(checkpoint_ns, str) else ""
            checkpoint_id = str(row["checkpoint_id"]) if row.get("checkpoint_id") else None
            messages = self._checkpoint_messages(
                thread_id=thread_id,
                checkpoint_ns=resolved_checkpoint_ns,
                checkpoint_id=checkpoint_id,
            )
            snapshots.append(
                CheckpointThreadSnapshot(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
                    messages=messages,
                )
            )
        return snapshots

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
        checkpoint_ns = row.get("checkpoint_ns")
        resolved_checkpoint_ns = checkpoint_ns if isinstance(checkpoint_ns, str) else ""
        resolved_checkpoint_id = str(row["checkpoint_id"]) if row.get("checkpoint_id") else None
        messages = self._checkpoint_messages(
            thread_id=resolved_thread_id,
            checkpoint_ns=resolved_checkpoint_ns,
            checkpoint_id=resolved_checkpoint_id,
        )
        return CheckpointThreadSnapshot(
            thread_id=resolved_thread_id,
            checkpoint_id=resolved_checkpoint_id,
            checkpoint_ts=str(row["checkpoint_ts"]) if row.get("checkpoint_ts") else None,
            messages=messages,
        )


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
            # Recover anything that changed while we were disconnected before we
            # start streaming live notifications.
            if self._on_resync is not None:
                self._on_resync()
            await aconn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(STORE_CHANGE_CHANNEL)))
            logger.info("Listening for store changes on %r", STORE_CHANGE_CHANNEL)
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


def configure_default_document_store(database_url: str | None) -> BackendDocumentStore:
    """Configure the process-wide backend document store."""
    global _default_store
    _default_store.close()
    if database_url:
        pg_store = PostgresDocumentStore(database_url)
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
