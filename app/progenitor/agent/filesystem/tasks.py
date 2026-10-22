"""Tasks capabilities."""

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

from app.progenitor.agent.filesystem.base import _AsyncOnlyFileBackendMixin, _doc_to_text
from app.progenitor.tasks.documents import (
    card_to_markdown,
    markdown_to_card,
    validate_kanban_structure,
)

_BOARD_FILE = "board.json"


_RESERVED_PROJECT_IDS = {".", ".."}


class KanbanTaskDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async DeepAgent backend exposing the kanban board as a task directory.

    Mounted at ``/tasks/``: ``board.json`` is the structure document
    (columns + ``card_order``) and every card is one ``.md`` file (YAML
    frontmatter for the fields, markdown body for the description). Each card is
    its own ``kanban_card:<id>`` document in the shared Postgres store, so the
    agent edits one task per file and a write touches a single row. Persistence
    flows through the StateManager, so the existing LISTEN/NOTIFY pipeline pushes
    ``kanban.snapshot`` to the Tasks UI with no bespoke tool.

    Cards may optionally belong to a project, grouped by directory: an unscoped
    card lives at ``<id>.md``; a card in a project lives at
    ``<project_id>/<id>.md``. There is no project registry document — a project
    is just whatever value cards carry in their ``project_id`` field, so the
    directory materializes when the first card is written into it and
    disappears once the last one leaves (mirrors how there's no index document
    for the cards themselves, just ``card_order``). The enclosing directory is
    authoritative for ``project_id``, the same way the filename is authoritative
    for ``id``.
    """

    _ASYNC_ONLY = "KanbanTaskDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        # CompositeBackend strips the /tasks/ route prefix before delegating.
        return file_path.lstrip("/").removeprefix("tasks/")

    @staticmethod
    def _parse_card_name(name: str) -> tuple[str | None, str] | None:
        """Split a task filename into ``(project_id, card_id)``, or ``None``.

        ``None`` means the name isn't a card path at all (e.g. ``board.json``,
        or a nested path more than one directory deep).
        """
        project_id: str | None = None
        if "/" in name:
            project_id, _, name = name.partition("/")
            project_id = project_id.strip()
            if not project_id or project_id in _RESERVED_PROJECT_IDS or "/" in name:
                return None
        if name == _BOARD_FILE or not name.endswith(".md"):
            return None
        return project_id, name[: -len(".md")]

    async def _structure_text(self) -> str:
        return _doc_to_text(await self._sm.get_kanban_structure())

    async def _resolve_card(self, name: str) -> tuple[str, dict[str, Any]] | None:
        """Look up the card a virtual path names, requiring its directory to match."""
        parsed = self._parse_card_name(name)
        if parsed is None:
            return None
        project_id, card_id = parsed
        card = await self._sm.get_kanban_card(card_id)
        if card is None or (card.get("project_id") or None) != project_id:
            return None
        return card_id, card

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        name = self._name(file_path)
        if name == _BOARD_FILE:
            text = await self._structure_text()
        else:
            resolved = await self._resolve_card(name)
            if resolved is None:
                return ReadResult(error=f"File '{file_path}' not found")
            _, card = resolved
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
        parsed = self._parse_card_name(name)
        if parsed is None:
            return WriteResult(
                error=f"Unknown task file '{file_path}' (expected <id>.md or <project>/<id>.md)"
            )
        project_id, card_id = parsed
        try:
            card = markdown_to_card(content, card_id, project_id)
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
        resolved = await self._resolve_card(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        card_id, _ = resolved
        await self._sm.delete_kanban_card(card_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        board = await self._sm.get_kanban_board()
        cards = [card for card in board.get("cards") or [] if isinstance(card, dict)]
        scope = path.strip("/")
        if not scope:
            names = [_BOARD_FILE]
            projects: set[str] = set()
            for card in cards:
                project_id = str(card.get("project_id") or "").strip()
                if project_id:
                    projects.add(project_id)
                else:
                    names.append(f"{card['id']}.md")
            entries = [FileInfo(path=f"/{name}", is_dir=False) for name in names]
            entries.extend(
                FileInfo(path=f"/{project_id}/", is_dir=True) for project_id in sorted(projects)
            )
            return LsResult(entries=entries)
        entries = [
            FileInfo(path=f"/{scope}/{card['id']}.md", is_dir=False)
            for card in cards
            if str(card.get("project_id") or "").strip() == scope
        ]
        return LsResult(entries=entries)
