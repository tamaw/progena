"""Postgres-backed raw file blob storage for chat attachments.

A dedicated table -- distinct from the generic ``store`` document/JSONB table
in ``document_store.py`` -- because attachments are opaque binary blobs
(images, PDFs, spreadsheets, ...), not small JSON documents. It lives in the
same Postgres database as the LangGraph ``store`` table
(``PROGENITOR_DATABASE_URL``), so both the backend (writer, via
``attachment_routes.py``) and the Progenitor runtime (reader, via
``app/progenitor/agent/attachment_backend.py``) share one source of truth
without any SSE round-trip to the runtime.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttachmentBlobMeta:
    """Attachment row without the raw bytes -- cheap to list/browse."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str
    thread_id: str | None
    text_content: str | None
    extraction_error: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class AttachmentBlob(AttachmentBlobMeta):
    """One attachment row including its raw bytes."""

    data: bytes


class AttachmentBlobStore(Protocol):
    """Minimal blob-store API used by the backend attachment routes."""

    def create(
        self,
        *,
        id: str,
        filename: str,
        mime_type: str,
        data: bytes,
        checksum: str,
        text_content: str | None,
        extraction_error: str | None,
    ) -> None: ...

    def get(self, attachment_id: str) -> AttachmentBlob | None: ...

    def get_by_checksum(self, checksum: str) -> AttachmentBlobMeta | None: ...

    def list_metadata(
        self, *, thread_id: str | None = None, limit: int = 200
    ) -> list[AttachmentBlobMeta]: ...

    def set_thread_id(self, attachment_id: str, thread_id: str) -> None: ...

    def delete(self, attachment_id: str) -> bool: ...

    def close(self) -> None: ...


class InMemoryAttachmentBlobStore:
    """Small test/local implementation with the same shape as Postgres."""

    def __init__(self) -> None:
        self._rows: dict[str, AttachmentBlob] = {}

    def create(
        self,
        *,
        id: str,
        filename: str,
        mime_type: str,
        data: bytes,
        checksum: str,
        text_content: str | None,
        extraction_error: str | None,
    ) -> None:
        if id in self._rows:
            return
        self._rows[id] = AttachmentBlob(
            id=id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            checksum=checksum,
            thread_id=None,
            text_content=text_content,
            extraction_error=extraction_error,
            created_at=datetime.now(UTC),
            data=data,
        )

    def get(self, attachment_id: str) -> AttachmentBlob | None:
        return self._rows.get(attachment_id)

    def get_by_checksum(self, checksum: str) -> AttachmentBlobMeta | None:
        for row in self._rows.values():
            if row.checksum == checksum:
                return row
        return None

    def list_metadata(
        self, *, thread_id: str | None = None, limit: int = 200
    ) -> list[AttachmentBlobMeta]:
        rows = sorted(
            self._rows.values(), key=lambda row: row.created_at or datetime.min, reverse=True
        )
        if thread_id:
            rows = [row for row in rows if row.thread_id == thread_id]
        return list(rows[:limit])

    def set_thread_id(self, attachment_id: str, thread_id: str) -> None:
        row = self._rows.get(attachment_id)
        if row is not None and row.thread_id is None:
            self._rows[attachment_id] = replace(row, thread_id=thread_id)

    def delete(self, attachment_id: str) -> bool:
        return self._rows.pop(attachment_id, None) is not None

    def close(self) -> None:
        return None


class PostgresAttachmentBlobStore:
    """Synchronous Postgres client for the ``attachments`` blob table."""

    _META_COLUMNS = (
        "id, thread_id, filename, mime_type, size_bytes, checksum, "
        "text_content, extraction_error, created_at"
    )

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool = self._create_pool()

    def _create_pool(self) -> Any:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        return ConnectionPool(
            conninfo=self._database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self._pool.open()
        self._ensure_table()

    def close(self) -> None:
        self._pool.close()

    def _recreate_pool(self) -> None:
        self._pool = self._create_pool()
        self._pool.open()

    @contextmanager
    def _cursor(self):
        from psycopg_pool import PoolClosed

        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur
                return
        except PoolClosed:
            logger.warning("Attachment blob store pool was closed; recreating it")
            self._recreate_pool()
            with self._pool.connection() as conn, conn.cursor() as cur:
                yield cur

    def _ensure_table(self) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    data BYTEA NOT NULL,
                    text_content TEXT,
                    extraction_error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS attachments_checksum_idx "
                "ON attachments (checksum)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS attachments_thread_id_idx ON attachments (thread_id)"
            )

    @staticmethod
    def _meta_from_row(row: dict[str, Any]) -> AttachmentBlobMeta:
        return AttachmentBlobMeta(
            id=row["id"],
            filename=row["filename"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            checksum=row["checksum"],
            thread_id=row.get("thread_id"),
            text_content=row.get("text_content"),
            extraction_error=row.get("extraction_error"),
            created_at=row.get("created_at"),
        )

    def create(
        self,
        *,
        id: str,
        filename: str,
        mime_type: str,
        data: bytes,
        checksum: str,
        text_content: str | None,
        extraction_error: str | None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO attachments
                    (id, filename, mime_type, size_bytes, checksum, data,
                     text_content, extraction_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    id,
                    filename,
                    mime_type,
                    len(data),
                    checksum,
                    data,
                    text_content,
                    extraction_error,
                ),
            )

    def get(self, attachment_id: str) -> AttachmentBlob | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM attachments WHERE id = %s", (attachment_id,))
            row = cast(dict[str, Any] | None, cur.fetchone())
        if row is None:
            return None
        meta = self._meta_from_row(row)
        return AttachmentBlob(**meta.__dict__, data=bytes(row["data"]))

    def get_by_checksum(self, checksum: str) -> AttachmentBlobMeta | None:
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {self._META_COLUMNS} FROM attachments WHERE checksum = %s",  # nosec B608 - _META_COLUMNS is a static class constant, not user input
                (checksum,),
            )
            row = cast(dict[str, Any] | None, cur.fetchone())
        return self._meta_from_row(row) if row is not None else None

    def list_metadata(
        self, *, thread_id: str | None = None, limit: int = 200
    ) -> list[AttachmentBlobMeta]:
        with self._cursor() as cur:
            base_query = f"SELECT {self._META_COLUMNS} FROM attachments"  # nosec B608 - _META_COLUMNS is a static class constant, not user input
            if thread_id:
                cur.execute(
                    f"{base_query} WHERE thread_id = %s ORDER BY created_at DESC LIMIT %s",
                    (thread_id, limit),
                )
            else:
                cur.execute(f"{base_query} ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cast(list[dict[str, Any]], cur.fetchall())
        return [self._meta_from_row(row) for row in rows]

    def set_thread_id(self, attachment_id: str, thread_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE attachments SET thread_id = %s WHERE id = %s AND thread_id IS NULL",
                (thread_id, attachment_id),
            )

    def delete(self, attachment_id: str) -> bool:
        with self._cursor() as cur:
            cur.execute("DELETE FROM attachments WHERE id = %s", (attachment_id,))
            return bool(cur.rowcount > 0)


_default_store: AttachmentBlobStore = InMemoryAttachmentBlobStore()


def configure_attachment_blob_store(database_url: str | None) -> AttachmentBlobStore:
    """Configure the process-wide attachment blob store."""
    global _default_store
    _default_store.close()
    if database_url:
        pg_store = PostgresAttachmentBlobStore(database_url)
        pg_store.open()
        store: AttachmentBlobStore = pg_store
        logger.info("Attachment blob store connected to Postgres")
    else:
        store = InMemoryAttachmentBlobStore()
        logger.warning("PROGENITOR_DATABASE_URL is not set; attachment blob store is in-memory")
    _default_store = store
    return _default_store


def set_default_attachment_blob_store(store: AttachmentBlobStore) -> None:
    """Install a store explicitly, primarily for tests."""
    global _default_store
    _default_store = store


def get_attachment_blob_store() -> AttachmentBlobStore:
    return _default_store
