"""Content capabilities."""

from __future__ import annotations

import re
from pathlib import Path
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

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin
from src.progena.documents.catalog import check_entry_schema
from src.progena.instructions import check_instruction_write


class ContentDocumentDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing Postgres-backed content documents as a directory.

    Backs ``/instructions/`` and ``/memories/`` (whole-kind) plus one
    ``/skills/<marketplace>__<plugin>/`` mount per installed marketplace
    plugin (``path_prefix`` scopes the backend to just that segment of the
    ``"skill"`` kind, so the mount only ever sees that plugin's own rows).
    Progena's own native skills are a plain disk mount now (see
    ``backends.py``), not this backend.
    """

    _ASYNC_ONLY = "ContentDocumentDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any, kind: str, *, path_prefix: str | None = None) -> None:
        self._sm = state_manager
        self._kind = kind
        self._path_prefix = path_prefix

    def _name(self, file_path: str) -> str:
        name = file_path.lstrip("/")
        return f"{self._path_prefix}/{name}" if self._path_prefix else name

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        name = self._name(file_path)
        content = await self._sm.get_content_document(self._kind, name)
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
        name = self._name(file_path)
        if not name or name.endswith("/"):
            return WriteResult(error=f"Invalid {self._kind} file path '{file_path}'")
        await self._sm.save_content_document(self._kind, name, content)
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
        name = self._name(file_path)
        content = await self._sm.get_content_document(self._kind, name)
        if content is None:
            return WriteResult(error=f"File '{file_path}' not found")
        await self._sm.delete_content_document(self._kind, name)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        rows = await self._sm.list_content_documents(self._kind)
        prefix = self._name(path)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"
        strip = f"{self._path_prefix}/" if self._path_prefix else ""
        files: list[str] = []
        dirs: set[str] = set()
        for rel, _content in rows:
            if not rel.startswith(prefix):
                continue
            remainder = rel[len(prefix) :]
            head, sep, _tail = remainder.partition("/")
            if sep:
                dirs.add(head)
            else:
                files.append(rel)
        scoped_prefix = prefix[len(strip) :] if strip and prefix.startswith(strip) else prefix
        entries = [FileInfo(path=f"/{scoped_prefix}{d}", is_dir=True) for d in sorted(dirs)]
        entries += [
            FileInfo(path=f"/{f[len(strip) :] if strip else f}", is_dir=False)
            for f in sorted(files)
        ]
        return LsResult(entries=entries)


class UserAgentDirectoryBackend(ContentDocumentDirectoryBackend):
    """Validated read/write user manifests mounted at ``/agents/user/``.

    Save through StateManager so agent discovery and the existing store
    LISTEN/NOTIFY pipeline observe the same document as the Agents page.
    """

    def __init__(self, state_manager: Any, *, native_root: Path) -> None:
        super().__init__(state_manager, "agent", path_prefix="user")
        self._native_root = native_root

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        from src.progena.agent.object_agents.manifest import (
            discover_object_agents,
            parse_front_matter,
            parse_object_agent_manifest,
        )

        relative = file_path.lstrip("/")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*/SYSTEM\.md", relative):
            return WriteResult(error="Expected /agents/user/<kebab-case-name>/SYSTEM.md")
        try:
            raw = parse_front_matter(content, source=file_path)
            check_entry_schema("Agent front matter", raw, "agent-manifest.schema.v1.json")
            manifest = parse_object_agent_manifest(content, source=file_path, source_kind="user")
            slug = re.sub(r"[^a-z0-9]+", "-", manifest.name.strip().lower()).strip("-")
            if relative != f"{slug or 'agent'}/SYSTEM.md":
                raise ValueError(
                    "Agent name must match its directory; renaming requires a new path"
                )
            for source in await discover_object_agents(self._sm, self._native_root):
                if source.name == manifest.name and source.relative_path != self._name(file_path):
                    raise ValueError(
                        f"Agent name {manifest.name!r} is already owned by {source.source}"
                    )
        except ValueError as exc:
            return WriteResult(error=f"Invalid agent manifest: {exc}")
        return await super().awrite(file_path, content)


class InstructionDocumentDirectoryBackend(ContentDocumentDirectoryBackend):
    """Instructions mounted at ``/instructions/``, with validated writes.

    Every write must parse as an instruction template; bundled agent
    instructions are read-only and runtime instructions keep their
    placeholders (:func:`check_instruction_write`). Failures surface as a
    ``WriteResult.error`` so the agent sees the reason.
    """

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        try:
            previous = await self._sm.get_content_document(self._kind, name)
            check_instruction_write(name, previous, content, await self._sm.list_document_types())
        except (ValueError, PermissionError) as exc:
            return WriteResult(error=f"Invalid instruction: {exc}")
        return await super().awrite(file_path, content)
