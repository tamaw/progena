"""Backend access to Postgres-backed LangGraph documents."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

PROGENITOR_STATE_PREFIX = "progenitor.state"
PROGENITOR_LOG_PREFIX = "progenitor.logs"
BACKEND_STATE_PREFIX = "backend.state"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredDocument:
    """One document loaded from the backend document store."""

    payload: Any
    updated_at: datetime | None = None


class BackendDocumentStore(Protocol):
    """Minimal document-store API used by backend routes."""

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None: ...

    def save_document(self, prefix: str, key: str, payload: Any) -> None: ...

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def get_log_sources(self) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class InMemoryDocumentStore:
    """Small test/local implementation with the same payload shape as Postgres."""

    def __init__(self) -> None:
        self._documents: dict[tuple[str, str], StoredDocument] = {}

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        document = self._documents.get((prefix, key))
        if document is not None:
            return document
        if default is None:
            return None
        return StoredDocument(default, None)

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        self._documents[(prefix, key)] = StoredDocument(payload, datetime.now(UTC))

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

    def close(self) -> None:
        return None


class PostgresDocumentStore:
    """Synchronous Postgres client for the LangGraph ``store`` table."""

    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(
            conninfo=database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open()
        self._ensure_store_table()

    def close(self) -> None:
        self._pool.close()

    def _ensure_store_table(self) -> None:
        """Create the LangGraph-compatible store table when running in a fresh DB."""
        with self._pool.connection() as conn, conn.cursor() as cur:
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

    def get_document(
        self, prefix: str, key: str, default: Any | None = None
    ) -> StoredDocument | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
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

    def save_document(self, prefix: str, key: str, payload: Any) -> None:
        value = {"payload": payload}
        with self._pool.connection() as conn, conn.cursor() as cur:
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

    def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["prefix LIKE %s"]
        params: list[Any] = [f"{PROGENITOR_LOG_PREFIX}.%"]
        if source:
            clauses.append("prefix = %s")
            params.append(f"{PROGENITOR_LOG_PREFIX}.{source}")
        if level:
            clauses.append("lower(value->>'level') = %s")
            params.append(level.lower())
        limit_sql = "LIMIT %s" if limit > 0 else ""
        if limit > 0:
            params.append(limit)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT prefix, key, value, updated_at
                FROM store
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(value->>'timestamp', value->>'time', updated_at::text) DESC
                {limit_sql}
                """,
                params,
            )
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [_log_row_to_entry(row) for row in reversed(rows)]

    def get_log_sources(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
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
