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
from croniter import croniter
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

from ..schedule.engine import parse_schedule_datetime
from ..schemas import load_schema
from ..state.models import ScheduleEntry
from ..structured import validate_against_schema
from ..utils import utcnow

logger = logging.getLogger(__name__)

JsonDoc = dict[str, Any] | list[Any]


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
        _check_entry_schema(f"server '{name}'", cfg, "mcp_server_entry")
        url = str(cfg.get("url") or cfg.get("endpoint") or "").strip()
        if not url:
            raise ValueError(f"server '{name}' is missing 'url'")
        transport = str(cfg.get("transport") or "http").strip().lower()
        if transport != "http":
            raise ValueError(
                f"server '{name}' transport '{transport}' is unsupported (remote HTTP only)"
            )
    return data


def validate_registry_document(content: str) -> JsonDoc:
    """Parse + validate an ``agents.json`` payload (the ``registry`` document).

    Shape: ``{"agents": [...], "tools": [...]}``, no other top-level keys.
    Each agent's shape mirrors ``data/schemas/agent_entry.schema.v1.json``.
    ``tools`` is runtime-managed (MCP-derived); this only checks its shape, not
    its contents.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    extra_keys = set(data) - {"agents", "tools"}
    if extra_keys:
        raise ValueError(f"unexpected top-level key(s): {', '.join(sorted(extra_keys))}")

    agents = data.get("agents")
    if not isinstance(agents, list):
        raise ValueError("missing 'agents' array")
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"agent {index} must be an object")
        _check_entry_schema(f"agent {index}", agent, "agent_entry")
        if not str(agent.get("name") or "").strip():
            raise ValueError(f"agent {index} is missing 'name'")

    tools = data.get("tools")
    if not isinstance(tools, list):
        raise ValueError("missing 'tools' array")

    return data


def validate_knowledge_document(content: str) -> JsonDoc:
    """Parse + validate a ``knowledge.json`` payload (knowledge-base settings)."""
    from ..knowledge import validate_knowledge_document as _validate

    return _validate(content)


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


def card_to_markdown(card: dict[str, Any]) -> str:
    """Render one card as YAML frontmatter (fields) + markdown body (description)."""
    fields = {k: v for k, v in card.items() if k != "description"}
    body = str(card.get("description") or "").strip("\n")
    frontmatter = yaml.safe_dump(
        fields, sort_keys=True, default_flow_style=False, allow_unicode=True
    ).strip()
    if body:
        return f"---\n{frontmatter}\n---\n\n{body}\n"
    return f"---\n{frontmatter}\n---\n"


def markdown_to_card(content: str, card_id: str, project_id: str | None = None) -> dict[str, Any]:
    """Parse a ``tasks/<id>.md`` or ``tasks/<project>/<id>.md`` file into a card dict.

    The file's location is authoritative, same as e.g. the registry's
    ``name``-as-key convention: the filename sets ``id`` and the enclosing
    directory (if any) sets ``project_id``, overriding whatever the frontmatter
    says. The markdown body becomes the card ``description``. Validation is
    light (a title is required) since the backend re-normalizes the assembled
    board on read.
    """
    fields, body = _split_frontmatter(content)
    card: dict[str, Any] = dict(fields)
    card["id"] = card_id
    if project_id is not None:
        card["project_id"] = project_id
    else:
        card.pop("project_id", None)
    body = body.strip("\n")
    if body:
        card["description"] = body
    if not str(card.get("title") or "").strip():
        raise ValueError("card is missing a 'title'")
    return card


def _doc_to_text(doc: JsonDoc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _check_entry_schema(label: str, entry: dict[str, Any], schema_name: str) -> None:
    """Validate one entry dict against its JSON Schema (the enforced floor).

    The JSON Schema files under ``data/schemas/`` describe the same per-entry
    shapes as this module's hand-rolled semantic checks (croniter parsing,
    timestamp parsing, ...). Without this, the two sources of truth can
    silently diverge; this makes the schema an enforced floor instead of just
    documentation. Raises ``ValueError`` (matching this module's other
    validators) on a schema violation or a missing/malformed schema file.
    """
    try:
        schema = load_schema(schema_name)
    except FileNotFoundError as exc:
        raise ValueError(f"{label}: schema '{schema_name}' not found: {exc}") from exc
    error = validate_against_schema(entry, schema)
    if error is not None:
        raise ValueError(f"{label} does not conform to schema: {error}")


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
    unique logic (id-as-key convention, exactly-one-of validation, audit-log
    diffing) this helper would have to special-case away.
    """

    async def load() -> JsonDoc:
        result = await get()
        return result if isinstance(result, dict) else default

    return StateFile(name=name, load=load, save=save, validate=validate, on_change=on_change)


def create_mcp_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``mcp.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "mcp.json",
        get=state_manager.get_mcp_config,
        save=state_manager.save_mcp_config,
        validate=validate_mcp_document,
        default={"mcpServers": {}},
        on_change=on_change,
    )


def create_registry_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``agents.json`` state file backed by the StateManager.

    Backs the ``registry`` document (``{"agents": [...], "tools": [...]}``);
    named ``agents.json`` to match the backend's existing external naming for
    this document (see ``StateReader._document_for_path``).
    """

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("agents.json must be a JSON object")
        await state_manager.save_registry(doc)

    return _simple_state_file(
        "agents.json",
        get=state_manager.get_registry,
        save=save,
        validate=validate_registry_document,
        default={"agents": [], "tools": []},
        on_change=on_change,
    )


def create_knowledge_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``knowledge.json`` state file backed by the StateManager.

    Editing it is the supported way for the agent to manage knowledge-base
    settings; the documents/vectors themselves live in SQL and are ingested via
    the UI. Every write is validated and persists through the normal store path,
    so the Knowledge page updates live via the LISTEN/NOTIFY pipeline.
    """
    return _simple_state_file(
        "knowledge.json",
        get=state_manager.get_knowledge_config,
        save=state_manager.save_knowledge_config,
        validate=validate_knowledge_document,
        default={"bases": []},
        on_change=on_change,
    )


def _reject_health_write(_content: Any) -> JsonDoc:
    raise ValueError("health.json is read-only - it's written by the heartbeat loop, not the agent")


def create_health_state_file(state_manager: Any) -> StateFile:
    """Build the read-only ``health.json`` state file backed by the StateManager.

    Surfaces the heartbeat loop's own system status (status, cycle_count,
    thread_id, pid, git commit ``version``, ``started_at``, LLM model/reasoning
    config) for self-inspection - the `checking-status` skill reads this to
    answer "are you running / what version / what model" questions. Read-only:
    the document is entirely runtime-owned, so both ``save`` and ``validate``
    reject with the same explanatory error rather than silently no-op'ing.
    """

    async def save(_doc: JsonDoc) -> None:
        _reject_health_write(_doc)

    return _simple_state_file(
        "health.json",
        get=state_manager.get_health,
        save=save,
        validate=_reject_health_write,
        default={},
    )


_BOARD_FILE = "board.json"
_RESERVED_PROJECT_IDS = {".", ".."}


class KanbanTaskDirectoryBackend:
    """Async DeepAgent backend exposing the kanban board as a task directory.

    Mounted at ``/state/tasks/``: ``board.json`` is the structure document
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
        # CompositeBackend strips the /state/tasks/ route prefix before delegating.
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


def validate_schedule_document(content: str) -> JsonDoc:
    """Parse + validate the whole ``schedule.json`` payload.

    ``schedule.json`` is a JSON object keyed by schedule id (mirrors
    ``mcp.json``'s ``mcpServers`` map): an entry's key is authoritative for its
    id, so entry bodies do not repeat it. Also checks that each entry's
    ``cron_expression``/``scheduled_at`` is actually parseable, not just a
    string - this is the only validation a write gets, since there's no
    dedicated create/update/delete tool; the ``managing-schedules`` skill
    reads, edits, and writes this one file directly, the same way
    ``managing-mcp`` does for ``mcp.json``.

    Every field is type-checked with ``isinstance`` (not just coerced) so a
    malformed entry (e.g. a non-string ``message``) is rejected here with a
    clean ``ValueError`` rather than reaching ``ScheduleEntry(**fields)`` at
    save time and raising an uncaught ``pydantic.ValidationError``. Exactly
    one of ``cron_expression``/``scheduled_at`` must be set, mirroring the
    check the old ``add_scheduled_task`` tool used to make before this
    document became agent-editable.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object keyed by schedule id")

    for entry_id, item in data.items():
        if not isinstance(item, dict):
            raise ValueError(f"entry '{entry_id}' must be an object")
        _check_entry_schema(f"entry '{entry_id}'", item, "schedule_entry")

        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"entry '{entry_id}' is missing 'message'")

        cron_expression = item.get("cron_expression")
        if cron_expression is not None and not isinstance(cron_expression, str):
            raise ValueError(f"entry '{entry_id}' has a non-string cron_expression")
        scheduled_at = item.get("scheduled_at")
        if scheduled_at is not None and not isinstance(scheduled_at, str):
            raise ValueError(f"entry '{entry_id}' has a non-string scheduled_at")
        timezone = item.get("timezone")
        if timezone is not None and not isinstance(timezone, str):
            raise ValueError(f"entry '{entry_id}' has a non-string timezone")
        user_request = item.get("user_request")
        if user_request is not None and not isinstance(user_request, str):
            raise ValueError(f"entry '{entry_id}' has a non-string user_request")
        paused = item.get("paused", False)
        if not isinstance(paused, bool):
            raise ValueError(f"entry '{entry_id}' has a non-bool paused")

        if bool(cron_expression) == bool(scheduled_at):
            raise ValueError(
                f"entry '{entry_id}' must set exactly one of "
                "cron_expression/scheduled_at, not both or neither"
            )

        if cron_expression:
            try:
                croniter(str(cron_expression), utcnow())
            except Exception as exc:
                raise ValueError(
                    f"entry '{entry_id}' has invalid cron_expression: {cron_expression}"
                ) from exc

        if scheduled_at and parse_schedule_datetime(scheduled_at) is None:
            raise ValueError(
                f"entry '{entry_id}' has invalid scheduled_at timestamp: {scheduled_at}"
            )

    return data


def create_schedule_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``schedule.json`` state file backed by the StateManager.

    ``StateManager.get_schedule()``/``save_schedule()`` already use the same
    object-keyed-by-id shape this file exposes to the agent - the only
    presentation-layer difference is that ``id`` lives solely in the object
    key here, not duplicated inside each entry's value (matching
    ``data/schemas/schedule_entry.schema.v1.json``'s documented shape).

    ``save`` diffs the incoming entries against the current on-disk state and
    emits the same ``log_schedule_event`` audit-trail entries
    (``entry_added``/``entry_removed``/``entry_updated``) that
    ``StateManager.add_schedule_entry``/``remove_schedule_entry``/
    ``update_schedule_entry`` emit, so this agent-facing write path produces
    the same audit trail regardless of which path made the change. One
    ``get_schedule`` read + one ``save_schedule`` write + one log call per
    changed entry - no redundant re-reads.
    """

    async def load() -> JsonDoc:
        entries = await state_manager.get_schedule()
        return {
            entry_id: {k: v for k, v in entry.model_dump(mode="json").items() if k != "id"}
            for entry_id, entry in entries.items()
        }

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("schedule.json must be a JSON object keyed by schedule id")
        entries: dict[str, ScheduleEntry] = {}
        for entry_id, item in doc.items():
            if not isinstance(item, dict):
                raise ValueError(f"entry '{entry_id}' must be an object")
            fields = {k: v for k, v in item.items() if k != "id"}
            entries[entry_id] = ScheduleEntry(id=entry_id, **fields)

        previous = {
            entry_id: entry.model_dump(mode="json")
            for entry_id, entry in (await state_manager.get_schedule()).items()
        }
        await state_manager.save_schedule(entries)

        seen_ids: set[str] = set()
        for entry_id, entry in entries.items():
            seen_ids.add(entry_id)
            current = entry.model_dump(mode="json")
            prior = previous.get(entry_id)
            if prior is None:
                await state_manager.log_schedule_event(
                    {
                        "event": "entry_added",
                        "schedule_id": entry_id,
                        "description": current.get("description"),
                    }
                )
            elif prior != current:
                changed = sorted(key for key in current if prior.get(key) != current.get(key))
                await state_manager.log_schedule_event(
                    {"event": "entry_updated", "schedule_id": entry_id, "updates": changed}
                )
        for removed_id in previous.keys() - seen_ids:
            await state_manager.log_schedule_event(
                {"event": "entry_removed", "schedule_id": removed_id}
            )

    return StateFile(
        name="schedule.json",
        load=load,
        save=save,
        validate=validate_schedule_document,
        on_change=on_change,
    )
