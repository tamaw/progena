"""DeepAgent backend exposing chat history as IRC-style logs under ``/conversations/``.

One read-only ``<date>_<title-slug>_<id8>.txt`` file per non-archived chat,
rendered from the LangGraph checkpointer and cached in the store by
``app.progenitor.conversations.service`` (see there for when each operation
re-renders). Archived chats have no file at all.
"""

from __future__ import annotations

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, grep_matches_from_files, slice_read_response
from wcmatch import fnmatch

from ..conversations.service import ConversationTranscripts
from .state_backend import _AsyncOnlyFileBackendMixin

_READ_ONLY_ERROR = "Conversation logs are generated from chat history and cannot be {verb}"


class ConversationDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async, read-only backend over rendered chat transcripts."""

    _ASYNC_ONLY = "ConversationDirectoryBackend is async-only; use the async file tools."

    def __init__(self, transcripts: ConversationTranscripts) -> None:
        self._transcripts = transcripts

    @staticmethod
    def _name(file_path: str) -> str:
        # CompositeBackend strips the /conversations/ route prefix before delegating.
        return file_path.lstrip("/").removeprefix("conversations/")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        content = await self._transcripts.read(self._name(file_path))
        if content is None:
            return ReadResult(error=f"File '{file_path}' not found")
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR.format(verb="written"))

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error=_READ_ONLY_ERROR.format(verb="edited"))

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY_ERROR.format(verb="deleted"))

    async def als(self, path: str) -> LsResult:
        files = await self._transcripts.list_files()
        return LsResult(entries=[FileInfo(path=f"/{file.name}", is_dir=False) for file in files])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Literal search over the cached transcripts; ``path``/``glob`` narrow by file name first."""
        target = self._name(path or "/").rstrip("/")

        def include(name: str) -> bool:
            if target and name != target:
                return False
            return not glob or fnmatch.fnmatch(name, glob, flags=fnmatch.BRACE)

        contents = await self._transcripts.contents(include)
        files = {f"/{name}": create_file_data(text) for name, text in contents.items()}
        return grep_matches_from_files(files, pattern, path or "/", glob)
