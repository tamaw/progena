"""DeepAgent backend exposing uploaded chat attachments as files.

Backed directly by the same Postgres ``attachments`` blob table the backend
writes to (``app/backend/attachment_blob_store.py``) -- a raw SQL read, not
routed through the LangGraph ``store``/StateManager document abstraction,
since attachments are opaque blobs the agent only needs to browse and read
as text (via their best-effort extracted content), not small JSON documents.

Read-only: uploads happen through the backend's HTTP API
(``POST /api/attachments``), never through this mount.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, slice_read_response

from .state_backend import _AsyncOnlyFileBackendMixin

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

_UPLOAD_ONLY_ERROR = "Attachments are uploaded through the chat UI, not {verb} by the agent"


class AttachmentDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async, read-only backend exposing ``/attachments/<filename>`` files.

    Filenames are not unique across uploads; both reads and listing resolve
    to the most recently uploaded row for a given name.
    """

    _ASYNC_ONLY = "AttachmentDirectoryBackend is async-only; use the async file tools."

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any | None = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            from psycopg_pool import AsyncConnectionPool

            pool: Any = AsyncConnectionPool(self._database_url, min_size=1, max_size=4, open=False)
            await pool.open()
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_ENSURE_TABLE_SQL)
            self._pool = pool
        return self._pool

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        from psycopg.rows import dict_row

        name = self._name(file_path)
        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT text_content, extraction_error, mime_type FROM attachments "
                "WHERE filename = %s ORDER BY created_at DESC LIMIT 1",
                (name,),
            )
            row = await cur.fetchone()
        if row is None:
            return ReadResult(error=f"File '{file_path}' not found")
        content = row.get("text_content")
        if not content:
            reason = (
                row.get("extraction_error")
                or f"no text could be extracted ({row.get('mime_type')})"
            )
            return ReadResult(error=f"'{file_path}' has no readable text content: {reason}")
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_UPLOAD_ONLY_ERROR.format(verb="written"))

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error=_UPLOAD_ONLY_ERROR.format(verb="edited"))

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_UPLOAD_ONLY_ERROR.format(verb="deleted"))

    async def als(self, path: str) -> LsResult:
        from psycopg.rows import dict_row

        pool = await self._get_pool()
        async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT DISTINCT ON (filename) filename FROM attachments "
                "ORDER BY filename, created_at DESC"
            )
            rows = await cur.fetchall()
        return LsResult(
            entries=[FileInfo(path=f"/{row['filename']}", is_dir=False) for row in rows]
        )

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
