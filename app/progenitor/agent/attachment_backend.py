"""DeepAgent backend exposing uploaded chat attachments as files.

Backed directly by the same Postgres ``attachments`` blob table the backend
writes to (``app/backend/attachment_blob_store.py``), via
``app.progenitor.attachments_db`` -- a raw SQL read, not routed through the
LangGraph ``store``/StateManager document abstraction, since attachments
are opaque blobs the agent only needs to browse and read as text (via
their best-effort extracted content), not small JSON documents.

Read-only: uploads happen through the backend's HTTP API
(``POST /api/attachments``), never through this mount. To convert a binary
attachment (PDF, DOCX, ...) to markdown on demand, use the
``convert_attachment`` system tool instead of writing through this mount.
"""

from __future__ import annotations

import logging

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

from .. import attachments_db
from .state_backend import _AsyncOnlyFileBackendMixin

logger = logging.getLogger(__name__)

_UPLOAD_ONLY_ERROR = "Attachments are uploaded through the chat UI, not {verb} by the agent"


class AttachmentDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async, read-only backend exposing ``/attachments/<filename>`` files.

    Filenames are not unique across uploads; both reads and listing resolve
    to the most recently uploaded row for a given name.
    """

    _ASYNC_ONLY = "AttachmentDirectoryBackend is async-only; use the async file tools."

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        name = self._name(file_path)
        row = await attachments_db.fetch_by_name(self._database_url, name)
        if row is None:
            return ReadResult(error=f"File '{file_path}' not found")
        content = row.get("text_content")
        if not content:
            reason = row.get("extraction_error") or (
                f"no text could be extracted ({row.get('mime_type')}); "
                "try the convert_attachment tool"
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
        filenames = await attachments_db.list_filenames(self._database_url)
        return LsResult(entries=[FileInfo(path=f"/{name}", is_dir=False) for name in filenames])

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])
