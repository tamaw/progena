"""Direct Postgres access to the backend's ``attachments`` blob table.

Shared by the DeepAgent virtual filesystem mount
(``agent/attachment_backend.py``, mounted at ``/attachments/``) and the
``convert_attachment`` system tool -- both need to read (and occasionally
update) the same table the backend writes to
(``src/backend/attachments/store.py``), via a raw SQL connection, not an
SSE round-trip to the backend process. One connection pool per database URL
is cached for the life of the process instead of opened per call/instance.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ENSURE_TABLE_SQL = """
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

_pools: dict[str, Any] = {}


async def get_pool(database_url: str) -> Any:
    """Return the shared, lazily-opened connection pool for one database URL."""
    pool = _pools.get(database_url)
    if pool is None:
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(database_url, min_size=1, max_size=4, open=False)
        await pool.open()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(_ENSURE_TABLE_SQL)
        _pools[database_url] = pool
    return pool


async def close_all_pools() -> None:
    """Close every cached pool, primarily for tests."""
    for pool in _pools.values():
        await pool.close()
    _pools.clear()


async def fetch_by_id(
    database_url: str, attachment_id: str, *, thread_id: str
) -> dict[str, Any] | None:
    """Return the full row (including raw bytes) for one attachment id,
    scoped to ``thread_id`` -- callers must not be able to read another
    thread's attachments by id.
    """
    from psycopg.rows import dict_row

    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM attachments WHERE id = %s AND thread_id = %s",
            (attachment_id, thread_id),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def fetch_by_name(
    database_url: str, filename: str, *, thread_id: str
) -> dict[str, Any] | None:
    """Return the most recently uploaded row (including raw bytes) for one
    filename, scoped to ``thread_id``."""
    from psycopg.rows import dict_row

    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM attachments WHERE filename = %s AND thread_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (filename, thread_id),
        )
        row = await cur.fetchone()
    return dict(row) if row is not None else None


async def fetch_bytes_by_name(
    database_url: str, filename: str, *, thread_id: str, max_bytes: int
) -> bytes | None:
    """Fetch a bounded prefix of the latest upload, without loading the entire blob."""
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")
    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT substring(data from 1 for %s) FROM attachments "
            "WHERE filename = %s AND thread_id = %s ORDER BY created_at DESC LIMIT 1",
            (max_bytes + 1, filename, thread_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    content = bytes(row[0])
    if len(content) > max_bytes:
        raise ValueError(f"Sandbox attachment input exceeds {max_bytes} bytes")
    return content


async def fetch_attachment(
    database_url: str, identifier: str, *, thread_id: str
) -> dict[str, Any] | None:
    """Resolve an attachment by id (``att_...``) or by filename, whichever it
    looks like, scoped to ``thread_id``."""
    identifier = identifier.strip().lstrip("/")
    if identifier.startswith("att_"):
        return await fetch_by_id(database_url, identifier, thread_id=thread_id)
    return await fetch_by_name(database_url, identifier, thread_id=thread_id)


async def list_filenames(database_url: str, *, thread_id: str) -> list[str]:
    """Return distinct filenames uploaded to ``thread_id``, most-recent row per name."""
    from psycopg.rows import dict_row

    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT DISTINCT ON (filename) filename FROM attachments "
            "WHERE thread_id = %s ORDER BY filename, created_at DESC",
            (thread_id,),
        )
        rows = await cur.fetchall()
    return [row["filename"] for row in rows]


async def update_text_content(
    database_url: str, attachment_id: str, text_content: str, *, thread_id: str
) -> None:
    """Persist a (re-)converted text body back onto an attachment row,
    scoped to ``thread_id`` so a turn can't rewrite another thread's
    attachment content.

    Used by the ``convert_attachment`` tool so a markitdown conversion the
    agent already paid for also improves later ``/attachments/<file>`` reads
    instead of being thrown away after one tool call.
    """
    pool = await get_pool(database_url)
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "UPDATE attachments SET text_content = %s, extraction_error = NULL "
            "WHERE id = %s AND thread_id = %s",
            (text_content, attachment_id, thread_id),
        )
