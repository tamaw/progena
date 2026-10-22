"""Virtual filesystem mount for agent-authored UI modules."""

from __future__ import annotations

import json
from typing import Any

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, slice_read_response

from src.progenitor.agent.filesystem.content import ContentDocumentDirectoryBackend
from src.progenitor.modules.contracts import (
    MAX_MODULE_BYTES,
    MAX_MODULE_FILE_BYTES,
    MAX_MODULE_FILES,
    ModuleValidationError,
    load_module_libraries,
    module_revision,
    validate_module_file,
    validate_module_name,
)

_CATALOGUE_FILE = "_catalogue.json"
_STATUS_FILE = "status.json"


class ModuleDirectoryBackend(ContentDocumentDirectoryBackend):
    """Validated module files plus read-only catalogue and status projections."""

    def __init__(self, state_manager: Any) -> None:
        super().__init__(state_manager, "module")

    @staticmethod
    def _split_file_path(file_path: str) -> tuple[str, str]:
        relative = file_path.lstrip("/")
        name, separator, path = relative.partition("/")
        if not separator or not name or not path:
            raise ModuleValidationError("Expected /modules/<name>/<path>.")
        validate_module_name(name)
        if name.startswith("_"):
            raise ModuleValidationError(f"Module name {name!r} is reserved.")
        return name, path

    @staticmethod
    def _json_read(payload: object, offset: int, limit: int) -> ReadResult:
        content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def _module_files(self, name: str) -> dict[str, str]:
        prefix = f"{name}/"
        return {
            path[len(prefix) :]: content
            for path, content in await self._sm.list_content_documents("module")
            if path.startswith(prefix)
        }

    async def _catalogue(self) -> dict[str, object]:
        document_types = await self._sm.list_document_types()
        return {
            "libraries": load_module_libraries()["libraries"],
            "document_types": [
                document_type.payload() for _, document_type in sorted(document_types.items())
            ],
            "host_api": {
                "mount": "export default async function mount(root, host) { ...; return cleanup; }",
                "documents": "host.documents.list(type, {limit, cursor}) and host.documents.get(type, id)",
                "document_types": "host.documentTypes()",
                "events": "host.events.subscribe(patterns, callback)",
                "data": "host.data.get/set/delete/list for this module's JSON values",
                "asset": "host.asset(path) reads a non-module asset",
                "theme": (
                    "host.theme.mode is 'light' or 'dark'; host.theme.get('--color-accent') reads a "
                    "token (host.theme.tokens has all); host.theme.onChange(cb) returns unsubscribe "
                    "and fires on light/dark switches. CSS var(--...) and [data-theme] selectors "
                    "update automatically; redraw JS-computed colours (canvas, WebGL) in onChange"
                ),
                "limits": {
                    "max_file_bytes": MAX_MODULE_FILE_BYTES,
                    "max_module_bytes": MAX_MODULE_BYTES,
                    "max_files": MAX_MODULE_FILES,
                },
            },
        }

    async def _status(self, name: str) -> dict[str, object]:
        files = await self._module_files(name)
        current_revision = module_revision(files)
        status = await self._sm.get_module_status(name)
        if status is None:
            return {
                "state": "not_loaded",
                "current_revision": current_revision,
                "note": "open the module in the UI (Modules page) to load it",
            }
        projected = {
            field: status.get(field)
            for field in ("state", "revision", "errors", "reported_at")
            if field in status
        }
        projected["current_revision"] = current_revision
        projected["stale"] = status.get("revision") != current_revision
        return projected

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        relative = file_path.lstrip("/")
        if relative == _CATALOGUE_FILE:
            return self._json_read(await self._catalogue(), offset, limit)
        try:
            name, path = self._split_file_path(file_path)
        except ModuleValidationError:
            return await super().aread(file_path, offset, limit)
        if path == _STATUS_FILE:
            return self._json_read(await self._status(name), offset, limit)
        return await super().aread(file_path, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        try:
            name, path = self._split_file_path(file_path)
            if path == _STATUS_FILE or (name == "_catalogue" and path == "json"):
                raise ModuleValidationError(f"{file_path} is a read-only virtual module file.")
            existing = await self._module_files(name)
            validate_module_file(name, path, content, existing)
        except ModuleValidationError as exc:
            return WriteResult(error=f"Invalid module: {exc}")
        return await super().awrite(file_path, content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        try:
            name, path = self._split_file_path(file_path)
            if path == _STATUS_FILE:
                raise ModuleValidationError(f"{file_path} is a read-only virtual module file.")
        except ModuleValidationError as exc:
            return EditResult(error=f"Invalid module: {exc}")
        read = await super().aread(file_path, 0, 10_000_000)
        if read.error is not None:
            return EditResult(error=f"Error: {read.error}")
        current = read.file_data["content"] if read.file_data else ""
        from deepagents.backends.utils import perform_string_replacement

        result = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        new_content, occurrences = result
        written = await self.awrite(file_path, new_content)
        if written.error is not None:
            return EditResult(error=f"Error: {written.error}")
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> WriteResult:
        try:
            _name, path = self._split_file_path(file_path)
            if path == _STATUS_FILE:
                raise ModuleValidationError(f"{file_path} is a read-only virtual module file.")
        except ModuleValidationError as exc:
            return WriteResult(error=f"Invalid module: {exc}")
        return await super().adelete(file_path)

    async def als(self, path: str) -> LsResult:
        relative = path.strip("/")
        result = await super().als(path)
        entries = list(result.entries or [])
        if not relative:
            entries.append(FileInfo(path=f"/{_CATALOGUE_FILE}", is_dir=False))
        elif "/" not in relative:
            try:
                validate_module_name(relative)
            except ModuleValidationError:
                return LsResult(entries=entries)
            if await self._module_files(relative):
                entries.append(FileInfo(path=f"/{relative}/{_STATUS_FILE}", is_dir=False))
        return LsResult(entries=sorted(entries, key=lambda entry: entry["path"]))
