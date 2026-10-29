"""Base capabilities."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    InvalidGlobPatternError,
    _normalize_path,
    _relative_to_root,
    compile_grep_include_glob,
    create_file_data,
    file_data_to_string,
    perform_string_replacement,
    slice_read_response,
)
from wcmatch import glob as wcglob

from src.progena.documents.values import JsonDoc

logger = logging.getLogger(__name__)


@dataclass
class StateFile:
    """One state document exposed as a file.

    ``load``/``save`` move the document to/from its store; ``validate`` parses
    file text into a document (raising ``ValueError`` on bad input); ``on_change``
    runs after a successful write (e.g. reload MCP discovery).
    """

    name: str
    load: Callable[[], Awaitable[JsonDoc]]
    save: Callable[[JsonDoc], Awaitable[None]]
    validate: Callable[[str], JsonDoc]
    on_change: Callable[[], Awaitable[None]] | None = None


def _doc_to_text(doc: JsonDoc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


class _AsyncOnlyFileBackendMixin:
    """Shared async surface for the virtual backends over Postgres documents.

    The Progena DeepAgent runs async, so only each backend's async surface
    (aread/awrite/aedit/als/aglob/agrep) is ever wired up. Subclasses set their
    own ``_ASYNC_ONLY`` message and mix this in instead of repeating the sync
    stubs verbatim.

    ``agrep``/``aglob`` are implemented once here on top of each subclass's
    own ``als`` and ``aread``: grep searches exactly the text ``read_file``
    shows (so its line numbers line up), and glob matches discoverable files.

    Also supplies ``adownload_files``, required by DeepAgents'
    ``BackendProtocol`` (used e.g. by ``MemoryMiddleware``/``SkillsMiddleware``
    to batch-load mount sources). It's implemented once here, generically, on
    top of each subclass's own ``aread`` — that already returns raw,
    non-line-numbered content (line-number formatting happens downstream, in
    the file-read tool, not in the backend), so it's safe to reuse verbatim.
    """

    _ASYNC_ONLY: str = "This backend is async-only; use the async file tools."

    def read(self, *args: Any, **kwargs: Any) -> ReadResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    def write(self, *args: Any, **kwargs: Any) -> WriteResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    def edit(self, *args: Any, **kwargs: Any) -> EditResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    def ls(self, *args: Any, **kwargs: Any) -> LsResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    def glob(self, *args: Any, **kwargs: Any) -> GlobResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    def grep(self, *args: Any, **kwargs: Any) -> GrepResult:
        raise NotImplementedError(self._ASYNC_ONLY)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files as raw bytes, via this backend's ``aread``.

        Response order matches input order; any read failure (unknown path,
        out-of-range offset) normalizes to ``file_not_found`` since these
        backends only fail reads that way.
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            result = await self.aread(path, 0, 10_000_000)  # type: ignore[attr-defined]
            if result.error is not None or result.file_data is None:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=FILE_NOT_FOUND)
                )
                continue
            content = file_data_to_string(result.file_data)
            responses.append(
                FileDownloadResponse(path=path, content=content.encode("utf-8"), error=None)
            )
        return responses

    async def _virtual_unlisted_file_exists(self, path: str) -> bool:
        """Whether a valid, readable unlisted file is addressable at ``path``.

        Most virtual files are discoverable through ``als``. Backends with stable
        identifiers may opt in for an exact historical filename without exposing
        aliases, generated children, or arbitrary readable paths to glob/grep.
        """
        return False

    async def _virtual_file_paths(self, path: str = "/") -> list[str]:
        """List files below one normalized virtual path without walking siblings."""
        normalized_path = _normalize_path(path)
        if normalized_path != "/":
            parent = normalized_path.rpartition("/")[0] or "/"
            parent_listing = await self.als(parent)  # type: ignore[attr-defined]
            for entry in parent_listing.entries or []:
                if entry["path"] == normalized_path and not entry.get("is_dir"):
                    return [normalized_path]
            if await self._virtual_unlisted_file_exists(normalized_path):
                return [normalized_path]

        paths: list[str] = []
        pending, seen = [normalized_path], {normalized_path}
        while pending:
            listing = await self.als(pending.pop())  # type: ignore[attr-defined]
            for entry in listing.entries or []:
                entry_path = entry["path"]
                if normalized_path != "/" and (
                    entry_path != normalized_path
                    and not entry_path.startswith(f"{normalized_path}/")
                ):
                    continue
                if entry.get("is_dir"):
                    if entry_path not in seen:
                        seen.add(entry_path)
                        pending.append(entry_path)
                else:
                    paths.append(entry_path)
        return paths

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        """Discoverable paths under ``path`` matching ``pattern`` (``**`` recurses)."""
        normalized_path = _normalize_path(path)
        effective = pattern.lstrip("/")
        matches: list[FileInfo] = []
        for file_path in await self._virtual_file_paths(normalized_path):
            relative = _relative_to_root(file_path, normalized_path)
            if wcglob.globmatch(relative, effective, flags=wcglob.BRACE | wcglob.GLOBSTAR):
                matches.append(FileInfo(path=file_path, is_dir=False))
        return GlobResult(matches=matches)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Literal search over selected rendered documents, as ``read_file`` shows them."""
        try:
            normalized_path = _normalize_path(path)
        except ValueError:
            return GrepResult(matches=[])

        matcher = None
        if glob:
            try:
                matcher = compile_grep_include_glob(glob)
            except InvalidGlobPatternError as exc:
                return GrepResult(error=str(exc))

        matches: list[GrepMatch] = []
        for file_path in await self._virtual_file_paths(normalized_path):
            if matcher is not None and not matcher(_relative_to_root(file_path, normalized_path)):
                continue
            try:
                result = await self.aread(file_path, 0, 10_000_000)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - one unreadable document must not hide the rest
                logger.warning("grep: skipping unreadable %s: %s", file_path, exc)
                continue
            if result.error is not None or result.file_data is None:
                continue
            if result.file_data.get("encoding") == "base64":
                continue
            for line_num, line in enumerate(file_data_to_string(result.file_data).split("\n"), 1):
                if pattern not in line:
                    continue
                if max_count is not None and len(matches) >= max_count:
                    return GrepResult(matches=matches, truncated=True)
                matches.append({"path": file_path, "line": line_num, "text": line})
        return GrepResult(matches=matches)


class StateDocumentBackend(_AsyncOnlyFileBackendMixin):
    """Async DeepAgent backend exposing state documents as virtual files."""

    _ASYNC_ONLY = "StateDocumentBackend is async-only; use the async file tools."

    def __init__(self, files: list[StateFile]) -> None:
        self._files: dict[str, StateFile] = {f.name: f for f in files}

    def _lookup(self, file_path: str) -> StateFile | None:
        return self._files.get(file_path.lstrip("/"))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        state_file = self._lookup(file_path)
        if state_file is None:
            return ReadResult(error=f"File '{file_path}' not found")
        file_data = create_file_data(_doc_to_text(await state_file.load()))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        state_file = self._lookup(file_path)
        if state_file is None:
            return WriteResult(error=f"Unknown state file '{file_path}'")
        try:
            doc = state_file.validate(content)
            await state_file.save(doc)
        except ValueError as exc:
            return WriteResult(error=f"Invalid {state_file.name}: {exc}")
        await self._after_write(state_file)
        return WriteResult(path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        state_file = self._lookup(file_path)
        if state_file is None:
            return EditResult(error=f"Error: unknown state file '{file_path}'")
        current = _doc_to_text(await state_file.load())
        result = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        try:
            doc = state_file.validate(new_content)
            await state_file.save(doc)
        except ValueError as exc:
            return EditResult(error=f"Error: edit produced invalid {state_file.name}: {exc}")
        await self._after_write(state_file)
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def als(self, path: str) -> LsResult:
        entries = [FileInfo(path=f"/{name}", is_dir=False) for name in sorted(self._files)]
        return LsResult(entries=entries)

    async def _after_write(self, state_file: StateFile) -> None:
        if state_file.on_change is None:
            return
        try:
            await state_file.on_change()
        except Exception as exc:  # noqa: BLE001 - surface as a log, never fail the write
            logger.warning("on_change for state file %s failed: %s", state_file.name, exc)


def _simple_state_file(
    name: str,
    *,
    get: Callable[[], Awaitable[JsonDoc]],
    save: Callable[[JsonDoc], Awaitable[None]],
    validate: Callable[[str], JsonDoc],
    default: JsonDoc,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build a ``StateFile`` for a document that's a thin get/save pass-through.

    Covers the common case (mcp.json, knowledge.json, agents.json): load just
    coerces a non-dict result to ``default``, save just forwards to the
    StateManager setter. Schedule's factory is NOT built on this - it has real
    unique logic (id-as-key convention, exactly-one-of validation, change
    diffing) this helper would have to special-case away.
    """

    async def load() -> JsonDoc:
        result = await get()
        return result if isinstance(result, dict) else default

    return StateFile(name=name, load=load, save=save, validate=validate, on_change=on_change)
