"""Virtual filesystem backend for catalogued markdown knowledge pages."""

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

from ..knowledge.pages import (
    INDEX_FILE,
    check_log_append,
    classify_page_path,
    render_directory_index,
    render_root_index,
    stamp_generated,
    validate_concept_page,
)
from ..utils import utcnow_isoformat
from .state_backend import _AsyncOnlyFileBackendMixin


class KnowledgePageBackend(_AsyncOnlyFileBackendMixin):
    _ASYNC_ONLY = "KnowledgePageBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _valid_path(path: str) -> bool:
        """Stored keys that no longer classify (e.g. legacy files) are hidden."""
        try:
            classify_page_path(path)
        except ValueError:
            return False
        return True

    @staticmethod
    def _split(file_path: str) -> tuple[str | None, str]:
        relative = file_path.strip("/")
        if not relative or relative == INDEX_FILE:
            return None, INDEX_FILE
        base, separator, path = relative.partition("/")
        return (
            (base, path if separator and path else INDEX_FILE) if separator else (base, INDEX_FILE)
        )

    async def _bases(self) -> list[dict[str, Any]]:
        payload = await self._sm.get_knowledge_config()
        bases = payload.get("bases", [])
        return [
            base for base in bases if isinstance(base, dict) and isinstance(base.get("id"), str)
        ]

    async def _base(self, base_id: str | None) -> dict[str, Any] | None:
        if base_id is None:
            return None
        return next((base for base in await self._bases() if base.get("id") == base_id), None)

    @staticmethod
    def _live(payload: dict[str, Any] | None) -> bool:
        return (
            isinstance(payload, dict)
            and not payload.get("deleted_at")
            and isinstance(payload.get("content"), str)
        )

    async def _index(self, base_id: str, path: str, base: dict[str, Any]) -> str:
        directory = path[: -len(INDEX_FILE)].rstrip("/")
        live = [
            (row_path, payload["content"])
            for row_base, row_path, payload in await self._sm.list_knowledge_pages()
            if row_base == base_id and self._live(payload)
        ]
        title = str(base.get("name") or base_id) if not directory else directory.rsplit("/", 1)[-1]
        return render_directory_index(
            title=title,
            directory=directory,
            live_pages=live,
            instructions=str(base.get("instructions") or ""),
        )

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        base_id, path = self._split(file_path)
        if base_id is None:
            content = render_root_index(await self._bases())
        else:
            base = await self._base(base_id)
            if base is None:
                return ReadResult(error=f"File '{file_path}' not found")
            if path == INDEX_FILE or path.endswith("/" + INDEX_FILE):
                content = await self._index(base_id, path, base)
            else:
                payload = await self._sm.get_knowledge_page(base_id, path)
                if not self._live(payload) or not self._valid_path(path):
                    return ReadResult(error=f"File '{file_path}' not found")
                content = payload["content"]
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(file_data=FileData(content=sliced, encoding="utf-8"))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        base_id, path = self._split(file_path)
        base = await self._base(base_id)
        if base is None:
            return WriteResult(
                error=f"Unknown knowledge base '{base_id}'. Create it in /config/knowledge.json first (managing-knowledge skill)."
            )
        try:
            kind = classify_page_path(path)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        if kind == "index":
            return WriteResult(
                error="index.md is generated from page front matter and cannot be written"
            )
        payload = await self._sm.get_knowledge_page(base_id, path)
        previous = payload.get("content") if self._live(payload) else None
        try:
            if kind == "log":
                check_log_append(previous, content)
            elif kind == "concept":
                validate_concept_page(content)
                content = stamp_generated(previous, content, utcnow_isoformat())
        except ValueError as exc:
            return WriteResult(error=str(exc))
        await self._sm.save_knowledge_page(base_id, path, content)
        return WriteResult(path=file_path)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
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
        return WriteResult(
            error="Knowledge pages cannot be deleted by agents; set 'status: deprecated' in the page front matter instead"
        )

    async def als(self, path: str) -> LsResult:
        bases = await self._bases()
        relative = path.strip("/")
        if not relative:
            entries = [FileInfo(path=f"/{base['id']}/", is_dir=True) for base in bases]
            entries.append(FileInfo(path="/index.md", is_dir=False))
            return LsResult(entries=entries)
        base_id, _, directory = relative.partition("/")
        if await self._base(base_id) is None:
            return LsResult(entries=[])
        dir_prefix = f"{directory}/" if directory else ""
        files: set[str] = {INDEX_FILE}
        dirs: set[str] = set()
        for row_base, row_path, payload in await self._sm.list_knowledge_pages():
            if (
                row_base != base_id
                or not self._live(payload)
                or not row_path.startswith(dir_prefix)
                or not self._valid_path(row_path)
            ):
                continue
            rest = row_path[len(dir_prefix) :]
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
            else:
                files.add(rest)
        prefix = f"/{base_id}/{dir_prefix}"
        entries = [FileInfo(path=f"{prefix}{name}/", is_dir=True) for name in sorted(dirs)]
        entries.extend(FileInfo(path=prefix + name, is_dir=False) for name in sorted(files))
        return LsResult(entries=entries)
