"""Repositories capabilities."""

from __future__ import annotations

from typing import Any

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    create_file_data,
    perform_string_replacement,
    slice_read_response,
)

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text
from src.progena.repositories.schema import validate_repo_document


class RepoDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing declared repositories as ``/config/repos/<id>.json``.

    One ``repo:<id>`` document per file, validated against
    ``repo_entry.schema.v1.json`` on write (the same rule the backend's
    ``/api/ui/repos`` routes use). Live git state is scanned by the heartbeat
    into the read-only ``/config/repo_scan.json``, never written here.
    """

    _ASYNC_ONLY = "RepoDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _repo_id(file_path: str) -> str | None:
        name = file_path.lstrip("/")
        if "/" in name or not name.endswith(".json"):
            return None
        return name.removesuffix(".json") or None

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        repo_id = self._repo_id(file_path)
        entry = await self._sm.get_repo(repo_id) if repo_id else None
        if entry is None:
            return ReadResult(error=f"File '{file_path}' not found")
        file_data = create_file_data(_doc_to_text(entry))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        repo_id = self._repo_id(file_path)
        if repo_id is None:
            return WriteResult(error=f"Unknown repo file '{file_path}' (expected <id>.json)")
        try:
            existing = await self._sm.get_repo(repo_id)
            entry = validate_repo_document(
                repo_id, content, created_at=(existing or {}).get("created_at")
            )
        except ValueError as exc:
            return WriteResult(error=f"Invalid repo {repo_id}.json: {exc}")
        await self._sm.save_repo(repo_id, entry)
        return WriteResult(path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        read = await self.aread(file_path, 0, 10_000_000)
        if read.error is not None:
            return EditResult(error=f"Error: {read.error}")
        current = read.file_data["content"] if read.file_data else ""
        result = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        written = await self.awrite(file_path, new_content)
        if written.error is not None:
            return EditResult(error=f"Error: {written.error}")
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> WriteResult:
        repo_id = self._repo_id(file_path)
        if repo_id is None or await self._sm.get_repo(repo_id) is None:
            return WriteResult(error=f"File '{file_path}' not found")
        await self._sm.delete_repo(repo_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        repos = await self._sm.list_repos()
        return LsResult(
            entries=[FileInfo(path=f"/{repo_id}.json", is_dir=False) for repo_id in sorted(repos)]
        )
