"""DeepAgent backend exposing uploaded chat attachments as files.

Backed directly by the same Postgres ``attachments`` blob table the backend
writes to (``src/backend/attachments/store.py``), via
``src.progena.attachments.store`` -- a raw SQL read, not routed through the
LangGraph ``store``/StateManager document abstraction, since attachments
are opaque blobs the agent only needs to browse and read as text (via
their best-effort extracted content), not small JSON documents.

Read-only: uploads happen through the backend's HTTP API
(``POST /api/attachments``), never through this mount. To convert a binary
attachment (PDF, DOCX, ...) to markdown on demand, use the
``convert_attachment`` system tool instead of writing through this mount.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

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

try:
    # deepagents' own extension classification: any of these are ALWAYS
    # wrapped by the built-in read_file tool as a multimodal content block
    # (see deepagents.middleware.filesystem's read_file), regardless of
    # what `encoding` a backend declares -- the check is purely on the file
    # extension. So for these extensions we must hand back real raw bytes,
    # base64-encoded, never our best-effort extracted text: handing back
    # plain text here (as this module used to, unconditionally) gets
    # mislabeled "base64" downstream and breaks every read of e.g. a .pdf
    # with "invalid base64-encoded value" once it reaches the LLM API.
    from deepagents.backends.utils import _EXTENSION_TO_FILE_TYPE as _MULTIMODAL_EXTENSIONS
except ImportError:  # pragma: no cover - defensive against a deepagents version bump
    _MULTIMODAL_EXTENSIONS = {}

import src.progena.attachments.store as attachments_db
from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin
from src.progena.sessions.context import get_current_thread_id

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
        thread_id = get_current_thread_id()
        if thread_id is None:
            return ReadResult(error="No active conversation thread; cannot resolve attachments.")
        row = await attachments_db.fetch_by_name(self._database_url, name, thread_id=thread_id)
        if row is None:
            return ReadResult(error=f"File '{file_path}' not found")

        if Path(name).suffix.lower() in _MULTIMODAL_EXTENSIONS:
            # deepagents' read_file always treats this extension as binary
            # multimodal content (image/audio/video/PDF-like "file"); offset
            # and limit don't apply -- see the module-level comment on
            # _MULTIMODAL_EXTENSIONS.
            raw = bytes(row["data"])
            encoded = base64.b64encode(raw).decode("ascii")
            return ReadResult(file_data=FileData(content=encoded, encoding="base64"))

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
        thread_id = get_current_thread_id()
        if thread_id is None:
            return LsResult(entries=[])
        filenames = await attachments_db.list_filenames(self._database_url, thread_id=thread_id)
        return LsResult(entries=[FileInfo(path=f"/{name}", is_dir=False) for name in filenames])

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return GrepResult(matches=[])
