"""DeepAgent backend that surfaces Progenitor state documents as files.

Each registered document becomes one virtual file under the mount (e.g.
``/state/mcp.json``). Reads map to a getter; writes/edits parse + validate the
file content, persist it via a setter, then run an optional side-effect (e.g.
live MCP reload).

Persistence still goes through the normal state path (Postgres ``store``), so the
LISTEN/NOTIFY pipeline and the UI keep working unchanged — this is a *file
interface* over the documents, not a separate store. The agent can therefore
read and edit state with its native file tools instead of bespoke tools.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import yaml
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
from deepagents.backends.utils import (
    create_file_data,
    perform_string_replacement,
    slice_read_response,
)

logger = logging.getLogger(__name__)

JsonDoc = dict[str, Any]


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


def validate_mcp_document(content: str) -> JsonDoc:
    """Parse + validate an ``mcp.json`` payload (remote HTTP servers only)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError("missing 'mcpServers' object")
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"server '{name}' must be an object")
        url = str(cfg.get("url") or cfg.get("endpoint") or "").strip()
        if not url:
            raise ValueError(f"server '{name}' is missing 'url'")
        transport = str(cfg.get("transport") or "http").strip().lower()
        if transport != "http":
            raise ValueError(
                f"server '{name}' transport '{transport}' is unsupported (remote HTTP only)"
            )
    return data


def validate_kanban_structure(content: str) -> JsonDoc:
    """Parse + validate a ``tasks/board.json`` (board structure) payload.

    The structure document carries board-level data only — columns and the
    ordered ``card_order`` list. Individual cards live in their own
    ``tasks/<id>.md`` files, so this guards only the structure shape.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    columns = data.get("columns", [])
    if not isinstance(columns, list):
        raise ValueError("'columns' must be a list")
    order = data.get("card_order", [])
    if not isinstance(order, list):
        raise ValueError("'card_order' must be a list")
    if "cards" in data:
        raise ValueError("board.json holds structure only; edit tasks/<id>.md for cards")
    return data


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from a markdown body."""
    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter opened with '---' but never closed")
    try:
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")
    return data, "\n".join(lines[end + 1 :])


def card_to_markdown(card: JsonDoc) -> str:
    """Render one card as YAML frontmatter (fields) + markdown body (description)."""
    fields = {k: v for k, v in card.items() if k != "description"}
    body = str(card.get("description") or "").strip("\n")
    frontmatter = yaml.safe_dump(
        fields, sort_keys=True, default_flow_style=False, allow_unicode=True
    ).strip()
    if body:
        return f"---\n{frontmatter}\n---\n\n{body}\n"
    return f"---\n{frontmatter}\n---\n"


def markdown_to_card(content: str, card_id: str) -> JsonDoc:
    """Parse a ``tasks/<id>.md`` file into a card dict.

    The filename is authoritative for ``id``; the markdown body becomes the
    card ``description``. Validation is light (a title is required) since the
    backend re-normalizes the assembled board on read.
    """
    fields, body = _split_frontmatter(content)
    card: JsonDoc = dict(fields)
    card["id"] = card_id
    body = body.strip("\n")
    if body:
        card["description"] = body
    if not str(card.get("title") or "").strip():
        raise ValueError("card is missing a 'title'")
    return card


def _doc_to_text(doc: JsonDoc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


class StateDocumentBackend:
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
        except ValueError as exc:
            return WriteResult(error=f"Invalid {state_file.name}: {exc}")
        await state_file.save(doc)
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
        except ValueError as exc:
            return EditResult(error=f"Error: edit produced invalid {state_file.name}: {exc}")
        await state_file.save(doc)
        await self._after_write(state_file)
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def als(self, path: str) -> LsResult:
        entries = [FileInfo(path=f"/{name}", is_dir=False) for name in sorted(self._files)]
        return LsResult(entries=entries)

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])

    async def _after_write(self, state_file: StateFile) -> None:
        if state_file.on_change is None:
            return
        try:
            await state_file.on_change()
        except Exception as exc:  # noqa: BLE001 - surface as a log, never fail the write
            logger.warning("on_change for state file %s failed: %s", state_file.name, exc)

    # The Progenitor DeepAgent runs async, so only the async surface is wired.
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


def create_mcp_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``mcp.json`` state file backed by the StateManager."""

    async def load() -> JsonDoc:
        result = await state_manager.get_mcp_config()
        return result if isinstance(result, dict) else {"mcpServers": {}}

    async def save(doc: JsonDoc) -> None:
        await state_manager.save_mcp_config(doc)

    return StateFile(
        name="mcp.json",
        load=load,
        save=save,
        validate=validate_mcp_document,
        on_change=on_change,
    )


_BOARD_FILE = "board.json"


class KanbanTaskDirectoryBackend:
    """Async DeepAgent backend exposing the kanban board as a task directory.

    Mounted at ``/state/tasks/``: ``board.json`` is the structure document
    (columns + ``card_order``) and every card is one ``<id>.md`` file (YAML
    frontmatter for the fields, markdown body for the description). Each card is
    its own ``kanban_card:<id>`` document in the shared Postgres store, so the
    agent edits one task per file and a write touches a single row. Persistence
    flows through the StateManager, so the existing LISTEN/NOTIFY pipeline pushes
    ``kanban.snapshot`` to the Tasks UI with no bespoke tool.
    """

    _ASYNC_ONLY = "KanbanTaskDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        # CompositeBackend strips the /state/tasks/ route prefix before delegating.
        return file_path.lstrip("/").removeprefix("tasks/")

    @staticmethod
    def _card_id(name: str) -> str | None:
        if name == _BOARD_FILE or not name.endswith(".md"):
            return None
        return name[: -len(".md")]

    async def _structure_text(self) -> str:
        return _doc_to_text(await self._sm.get_kanban_structure())

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        name = self._name(file_path)
        if name == _BOARD_FILE:
            text = await self._structure_text()
        else:
            card_id = self._card_id(name)
            card = await self._sm.get_kanban_card(card_id) if card_id else None
            if card is None:
                return ReadResult(error=f"File '{file_path}' not found")
            text = card_to_markdown(card)
        file_data = create_file_data(text)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        if name == _BOARD_FILE:
            try:
                structure = validate_kanban_structure(content)
            except ValueError as exc:
                return WriteResult(error=f"Invalid board.json: {exc}")
            await self._sm.save_kanban_structure(structure)
            return WriteResult(path=file_path)
        card_id = self._card_id(name)
        if not card_id:
            return WriteResult(error=f"Unknown task file '{file_path}' (expected <id>.md)")
        try:
            card = markdown_to_card(content, card_id)
        except ValueError as exc:
            return WriteResult(error=f"Invalid task {name}: {exc}")
        await self._sm.save_kanban_card(card_id, card)
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
        card_id = self._card_id(self._name(file_path))
        if not card_id:
            return WriteResult(error=f"Cannot delete '{file_path}'")
        await self._sm.delete_kanban_card(card_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        names = [_BOARD_FILE]
        names.extend(f"{cid}.md" for cid in await self._sm.list_kanban_card_ids())
        return LsResult(entries=[FileInfo(path=f"/{name}", is_dir=False) for name in names])

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])

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
