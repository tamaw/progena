"""DeepAgent backend that surfaces Progenitor state documents as files.

Each registered document becomes one virtual file under the mount (e.g.
``/config/mcp.json``). Reads map to a getter; writes/edits parse + validate the
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
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from croniter import croniter
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    create_file_data,
    file_data_to_string,
    grep_matches_from_files,
    perform_string_replacement,
    slice_read_response,
)
from wcmatch import glob as wcglob

from ..routines.store import (
    parse_routine_graph_document,
    routine_file_name,
    routine_id_from_path,
)
from ..runtime.thread_context import get_current_thread_id
from ..schedule.engine import parse_schedule_datetime
from ..schemas import check_entry_schema
from ..state.model_profiles import (
    default_model_profiles_payload,
    validate_model_profiles_document,
)
from ..state.models import ScheduleEntry
from ..state.preferences import default_preferences_payload, validate_preferences_document
from ..utils import utcnow, utcnow_isoformat
from ..workflows import (
    validate_workflow_definition_document,
    workflow_file_name,
    workflow_id_from_path,
)

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
        check_entry_schema(f"server '{name}'", cfg, "mcp_server_entry")
        url = str(cfg.get("url") or cfg.get("endpoint") or "").strip()
        if not url:
            raise ValueError(f"server '{name}' is missing 'url'")
        transport = str(cfg.get("transport") or "http").strip().lower()
        if transport != "http":
            raise ValueError(
                f"server '{name}' transport '{transport}' is unsupported (remote HTTP only)"
            )
    return data


def validate_plugins_document(content: str) -> JsonDoc:
    """Parse + validate a ``plugins.json`` payload (the ``plugins`` document).

    Shape: ``{"plugins": {"<marketplace>/<plugin>": {...}}}``, no other top-level keys.
    Each plugin entry's shape mirrors ``data/schemas/plugin_entry.schema.v1.json``.
    Delegates to :mod:`..plugins.schema` -- the same function the backend's
    ``/api/ui/plugins`` routes call directly on already-parsed payloads, so
    both write paths enforce identical rules.
    """
    from ..plugins.schema import validate_plugins_document as _validate

    return _validate(content)


def validate_marketplaces_document(content: str) -> JsonDoc:
    """Parse + validate a ``marketplaces.json`` payload (the ``marketplaces`` document).

    Shape: ``{"marketplaces": {"<name>": {...}}}``, no other top-level keys.
    Each entry's shape mirrors ``data/schemas/marketplace_entry.schema.v1.json``.
    Delegates to :mod:`..marketplaces` -- the same function the backend's
    ``/api/ui/marketplaces`` route calls directly on already-parsed payloads,
    so both write paths enforce identical rules.
    """
    from ..marketplaces import validate_marketplaces_document as _validate

    return _validate(content)


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
        check_entry_schema(f"agent {index}", agent, "agent_entry")
        if not str(agent.get("name") or "").strip():
            raise ValueError(f"agent {index} is missing 'name'")

    tools = data.get("tools")
    if not isinstance(tools, list):
        raise ValueError("missing 'tools' array")

    return data


def validate_repos_document(content: str) -> JsonDoc:
    """Parse + validate a ``repos.json`` payload (the ``repos`` document).

    Shape: ``{"repos": {"<id>": {...}}}``, no other top-level keys. Each entry
    declares a repository the operator wants tracked; git state (branches,
    commits, worktrees) is scanned separately by the heartbeat and never lives
    in this document. Delegates to :mod:`..repos.schema` -- the same function
    the backend's ``/api/ui/repos`` routes call directly on already-parsed
    payloads, so both write paths enforce identical rules.
    """
    from ..repos.schema import validate_repos_document as _validate

    return _validate(content)


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


def inbox_message_to_markdown(message: dict[str, Any]) -> str:
    """Render one inbox message as YAML frontmatter (fields) + markdown body."""
    fields = {k: v for k, v in message.items() if k != "body"}
    body = str(message.get("body") or "").strip("\n")
    frontmatter = yaml.safe_dump(
        fields, sort_keys=True, default_flow_style=False, allow_unicode=True
    ).strip()
    if body:
        return f"---\n{frontmatter}\n---\n\n{body}\n"
    return f"---\n{frontmatter}\n---\n"


def markdown_to_inbox_message(content: str, message_id: str) -> dict[str, Any]:
    """Parse an ``inbox/<id>.md`` file into a message dict.

    The filename is authoritative for ``id`` (same convention as kanban cards).
    The markdown body becomes ``body``. A non-empty ``title`` is the only hard
    requirement; ``read`` is coerced to a bool and ``created_at`` is stamped
    when absent so agent-written files need only a title and a body. ``thread_id``
    is likewise stamped from the current request's thread context when absent —
    it is system-owned (never agent-authored) so an attachment referenced from
    that thread can later be resolved with the same scoping the attachments API
    already enforces. Both are backfilled only when missing, so an edit never
    overwrites the values a message was originally created with. Unknown
    frontmatter keys round-trip untouched.
    """
    fields, body = _split_frontmatter(content)
    message: dict[str, Any] = dict(fields)
    message["id"] = message_id
    body = body.strip("\n")
    message["body"] = body
    if not str(message.get("title") or "").strip():
        raise ValueError("message is missing a 'title'")
    message["read"] = bool(message.get("read", False))
    if not str(message.get("created_at") or "").strip():
        message["created_at"] = utcnow_isoformat()
    if not str(message.get("thread_id") or "").strip():
        thread_id = get_current_thread_id()
        if thread_id:
            message["thread_id"] = thread_id
    message.setdefault("read_at", None)
    return message


def _doc_to_text(doc: JsonDoc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


class _AsyncOnlyFileBackendMixin:
    """Shared async surface for the virtual backends over Postgres documents.

    The Progenitor DeepAgent runs async, so only each backend's async surface
    (aread/awrite/aedit/als/aglob/agrep) is ever wired up. Subclasses set their
    own ``_ASYNC_ONLY`` message and mix this in instead of repeating the sync
    stubs verbatim.

    ``agrep``/``aglob`` are implemented once here on top of each subclass's
    own ``als`` and ``aread``: grep searches exactly the text ``read_file``
    shows (so its line numbers line up), glob matches the listed paths.

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

    async def _virtual_file_paths(self) -> list[str]:
        """Every file path this backend lists, walking into subdirectories."""
        paths: list[str] = []
        pending, seen = ["/"], {"/"}
        while pending:
            listing = await self.als(pending.pop())  # type: ignore[attr-defined]
            for entry in listing.entries or []:
                entry_path = entry["path"]
                if entry.get("is_dir"):
                    if entry_path not in seen:
                        seen.add(entry_path)
                        pending.append(entry_path)
                else:
                    paths.append(entry_path)
        return paths

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        """Listed paths under ``path`` matching ``pattern`` (``**`` recurses)."""
        base = (path or "/").rstrip("/")
        effective = pattern.lstrip("/")
        matches: list[FileInfo] = []
        for file_path in await self._virtual_file_paths():
            if base and not file_path.startswith(f"{base}/"):
                continue
            relative = file_path[len(base) + 1 :]
            if wcglob.globmatch(relative, effective, flags=wcglob.BRACE | wcglob.GLOBSTAR):
                matches.append(FileInfo(path=file_path, is_dir=False))
        return GlobResult(matches=matches)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Literal search over the rendered documents, as ``read_file`` shows them."""
        base = (path or "/").rstrip("/")
        files: dict[str, FileData] = {}
        for file_path in await self._virtual_file_paths():
            if base and file_path != base and not file_path.startswith(f"{base}/"):
                continue
            try:
                result = await self.aread(file_path, 0, 10_000_000)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - one unreadable document must not hide the rest
                logger.warning("grep: skipping unreadable %s: %s", file_path, exc)
                continue
            if result.error is None and result.file_data is not None:
                files[file_path] = result.file_data
        return grep_matches_from_files(files, pattern, path or "/", glob)


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


def _validate_mcp_tools_document(content: str) -> JsonDoc:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        raise ValueError('expected {"tools": [<mcp_tool_entry>, ...]}')
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"tool {index}: expected an object")
        check_entry_schema(f"tool '{tool.get('name', index)}'", tool, "mcp_tool_entry")
    return data


def create_mcp_tools_state_file(state_manager: Any) -> StateFile:
    """Build ``mcp_tools.json``: discovered MCP tools, toggleable one by one.

    The tool list is produced by MCP discovery (servers, names, descriptions
    are rewritten on every re-discovery), so the only edit accepted is a
    tool's ``enabled`` flag - the same flag the Tools page toggles. Any other
    change is rejected with an explanation. The runtime's store listener
    re-applies the flags on the change, so a disabled tool disappears from the
    agent's tool set on its next turn; discovery keeps the flag afterwards.
    """

    async def save(doc: JsonDoc) -> None:
        current = await state_manager.get_mcp_tools()
        current_tools = current.get("tools", []) if isinstance(current, dict) else []
        current_by_name = {
            str(tool["name"]): tool
            for tool in current_tools
            if isinstance(tool, dict) and tool.get("name")
        }
        if not isinstance(doc, dict):
            raise ValueError("expected a JSON object")
        # Everything outside `tools` (servers, updated_at) is discovery-owned;
        # an added key here would be silently dropped, so the edit would look
        # successful while changing nothing.
        current_rest = {k: v for k, v in current.items() if k != "tools"}
        edited_rest = {k: v for k, v in doc.items() if k != "tools"}
        if edited_rest != current_rest:
            raise ValueError(
                "only a tool's `enabled` flag inside `tools` can change - there are no "
                "other settings in this file; to disable a tool set "
                '`"enabled": false` on its entry in `tools`'
            )
        new_by_name = {str(tool["name"]): tool for tool in doc.get("tools", [])}
        if set(new_by_name) != set(current_by_name):
            raise ValueError(
                "tools can't be added or removed here - the list comes from MCP discovery; "
                "manage servers in /config/mcp.json and only flip a tool's `enabled` flag here"
            )
        for name, tool in new_by_name.items():
            existing = {k: v for k, v in current_by_name[name].items() if k != "enabled"}
            edited = {k: v for k, v in tool.items() if k != "enabled"}
            if edited != existing:
                raise ValueError(
                    f"tool '{name}': only `enabled` can be changed - its other fields come "
                    "from MCP discovery"
                )
        await state_manager.save_mcp_tools(
            {
                **current,
                "tools": [
                    {**tool, "enabled": bool(new_by_name[str(tool["name"])].get("enabled", True))}
                    for tool in current_tools
                    if isinstance(tool, dict) and tool.get("name")
                ],
            }
        )

    return _simple_state_file(
        "mcp_tools.json",
        get=state_manager.get_mcp_tools,
        save=save,
        validate=_validate_mcp_tools_document,
        default={"tools": []},
    )


def create_plugins_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``plugins.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "plugins.json",
        get=state_manager.get_plugins_config,
        save=state_manager.save_plugins_config,
        validate=validate_plugins_document,
        default={"plugins": {}},
        on_change=on_change,
    )


def create_marketplaces_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``marketplaces.json`` state file backed by the StateManager."""
    return _simple_state_file(
        "marketplaces.json",
        get=state_manager.get_marketplaces_config,
        save=state_manager.save_marketplaces_config,
        validate=validate_marketplaces_document,
        default={"marketplaces": {}},
        on_change=on_change,
    )


def create_registry_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``agents.json`` state file backed by the StateManager.

    Backs the ``registry`` document (``{"agents": [...], "tools": [...]}``);
    named ``agents.json`` to match the backend's document store key
    ``("progenitor", "state", "registry")``.
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


def create_repos_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``repos.json`` state file backed by the StateManager.

    Backs the ``repos`` document (``{"repos": {"<id>": {...}}}``); the operator
    declares repositories here (name, remote, local worktree path, read/write
    access) via the Repositories page or the agent directly. Live git state
    (branches, commits, worktrees) is scanned by the heartbeat into a separate
    ``repo_scan`` document, never written through this file.
    """

    async def save(doc: JsonDoc) -> None:
        if not isinstance(doc, dict):
            raise ValueError("repos.json must be a JSON object")
        await state_manager.save_repos(doc)

    return _simple_state_file(
        "repos.json",
        get=state_manager.get_repos,
        save=save,
        validate=validate_repos_document,
        default={"repos": {}},
        on_change=on_change,
    )


def _reject_repo_scan_write(_content: Any) -> JsonDoc:
    raise ValueError(
        "repo_scan.json is read-only - it's written by the heartbeat's repo-scan tick, not the agent"
    )


def create_repo_scan_state_file(state_manager: Any) -> StateFile:
    """Build the read-only ``repo_scan.json`` state file backed by the StateManager.

    Surfaces the heartbeat's last derived git state per declared repo (branch,
    dirty count, commit/branch counts, latest commit) so the agent can answer
    "what's the status of X" without shelling out - the on-disk data can lag the
    real repo by up to ``repo_scan_seconds``. Read-only: the document is entirely
    heartbeat-owned, so both ``save`` and ``validate`` reject with the same
    explanatory error rather than silently no-op'ing.
    """

    async def save(_doc: JsonDoc) -> None:
        _reject_repo_scan_write(_doc)

    return _simple_state_file(
        "repo_scan.json",
        get=state_manager.get_repo_scan,
        save=save,
        validate=_reject_repo_scan_write,
        default={"scanned_at": None, "repos": {}, "workspace_signal": {}},
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


def create_preferences_state_file(
    state_manager: Any,
    *,
    on_change: Callable[[], Awaitable[None]] | None = None,
) -> StateFile:
    """Build the ``preferences.json`` state file backed by the StateManager.

    Lets the agent read/update the user's profile (display name, timezone) the
    same way it manages knowledge/MCP settings. Every write is validated and
    persists through the normal store path, so the Settings page updates live
    via the LISTEN/NOTIFY pipeline.
    """
    return _simple_state_file(
        "preferences.json",
        get=state_manager.get_preferences_config,
        save=state_manager.save_preferences_config,
        validate=validate_preferences_document,
        default=default_preferences_payload(),
        on_change=on_change,
    )


def create_model_profiles_state_file(state_manager: Any) -> StateFile:
    """Build the provider/deployment map exposed as ``/config/profile.json``."""
    return _simple_state_file(
        "profile.json",
        get=state_manager.get_model_profiles_config,
        save=state_manager.save_model_profiles_config,
        validate=validate_model_profiles_document,
        default=default_model_profiles_payload(),
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


class InboxDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async DeepAgent backend exposing the user's inbox as a message directory.

    Mounted at ``/inbox/``: every message is one ``<id>.md`` file (YAML
    frontmatter for the fields, markdown body for the content). Each message is
    its own ``inbox_message:<id>`` document in the shared Postgres store, so
    the agent composes one message per file and a write touches a single row.
    Persistence flows through the StateManager, so the existing LISTEN/NOTIFY
    pipeline pushes ``inbox.snapshot`` to the Inbox UI with no bespoke tool.

    The directory is flat — no index document; the message list is a key-prefix
    scan. ``read``/``read_at`` are user-owned (set by the backend when the user
    opens a message) and should be left alone by the agent.
    """

    _ASYNC_ONLY = "InboxDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        # CompositeBackend strips the /inbox/ route prefix before delegating.
        return file_path.lstrip("/").removeprefix("inbox/")

    @staticmethod
    def _parse_message_name(name: str) -> str | None:
        """Return the message id a filename names, or ``None`` if not a message path."""
        if "/" in name or not name.endswith(".md"):
            return None
        message_id = name[: -len(".md")].strip()
        return message_id or None

    async def _resolve_message(self, name: str) -> tuple[str, dict[str, Any]] | None:
        message_id = self._parse_message_name(name)
        if message_id is None:
            return None
        message = await self._sm.get_inbox_message(message_id)
        if message is None:
            return None
        return message_id, message

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_message(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, message = resolved
        file_data = create_file_data(inbox_message_to_markdown(message))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        message_id = self._parse_message_name(name)
        if message_id is None:
            return WriteResult(error=f"Unknown inbox file '{file_path}' (expected <id>.md)")
        try:
            message = markdown_to_inbox_message(content, message_id)
        except ValueError as exc:
            return WriteResult(error=f"Invalid inbox message {name}: {exc}")
        await self._sm.save_inbox_message(message_id, message)
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
        resolved = await self._resolve_message(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        message_id, _ = resolved
        await self._sm.delete_inbox_message(message_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        messages = await self._sm.list_inbox_messages()
        ordered = sorted(
            (m for m in messages if isinstance(m, dict) and m.get("id")),
            key=lambda m: str(m.get("created_at") or ""),
            reverse=True,
        )
        entries = [FileInfo(path=f"/{message['id']}.md", is_dir=False) for message in ordered]
        return LsResult(entries=entries)


class WorkflowDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing workflow definitions as ``/workflows/*.json``."""

    _ASYNC_ONLY = "WorkflowDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("workflows/")

    @staticmethod
    def _parse_workflow_name(name: str) -> str | None:
        if "/" in name or not name.endswith(".json"):
            return None
        try:
            return workflow_id_from_path(name)
        except ValueError:
            return None

    async def _resolve_workflow(self, name: str) -> tuple[str, dict[str, Any]] | None:
        workflow_id = self._parse_workflow_name(name)
        if workflow_id is None:
            return None
        workflow = await self._sm.get_workflow_definition(workflow_id)
        if workflow is None:
            return None
        return workflow_id, workflow

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_workflow(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, workflow = resolved
        file_data = create_file_data(_doc_to_text(workflow))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        workflow_id = self._parse_workflow_name(name)
        if workflow_id is None:
            return WriteResult(error=f"Unknown workflow file '{file_path}' (expected <id>.json)")
        try:
            workflow = validate_workflow_definition_document(content)
        except ValueError as exc:
            return WriteResult(error=f"Invalid workflow {name}: {exc}")
        if workflow.get("id") != workflow_id:
            return WriteResult(error=f"Workflow id must match filename '{workflow_id}'")
        await self._sm.save_workflow_definition(workflow_id, workflow)
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
        resolved = await self._resolve_workflow(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        workflow_id, _ = resolved
        await self._sm.delete_workflow_definition(workflow_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        workflows = await self._sm.list_workflow_definitions()
        ordered = sorted(
            (
                workflow
                for workflow in workflows
                if isinstance(workflow, dict) and workflow.get("id")
            ),
            key=lambda workflow: (
                str(workflow.get("updated_at") or ""),
                str(workflow.get("created_at") or ""),
                str(workflow.get("id") or ""),
            ),
            reverse=True,
        )
        entries = [
            FileInfo(path=f"/{workflow_file_name(str(workflow['id']))}", is_dir=False)
            for workflow in ordered
        ]
        return LsResult(entries=entries)


class RoutineDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing routine graphs as ``/routines/*.json``.

    Mirrors ``WorkflowDirectoryBackend`` exactly: one JSON document per file,
    validated against ``graph.routine.schema.v1.json`` on write. The document
    ``id`` is the owning agent's id, so this gives the agent the same
    read/write/edit experience over routine graphs that it already has over
    workflow definitions.
    """

    _ASYNC_ONLY = "RoutineDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: Any) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("routines/")

    @staticmethod
    def _parse_routine_name(name: str) -> str | None:
        if "/" in name or not name.endswith(".json"):
            return None
        try:
            return routine_id_from_path(name)
        except ValueError:
            return None

    async def _resolve_routine(self, name: str) -> tuple[str, dict[str, Any]] | None:
        agent_id = self._parse_routine_name(name)
        if agent_id is None:
            return None
        graph = await self._sm.get_routine_graph(agent_id)
        if graph is None:
            return None
        return agent_id, graph

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return ReadResult(error=f"File '{file_path}' not found")
        _, graph = resolved
        file_data = create_file_data(_doc_to_text(graph))
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        agent_id = self._parse_routine_name(name)
        if agent_id is None:
            return WriteResult(error=f"Unknown routine file '{file_path}' (expected <id>.json)")
        try:
            graph = parse_routine_graph_document(content)
        except ValueError as exc:
            return WriteResult(error=f"Invalid routine {name}: {exc}")
        if graph.get("id") != agent_id:
            return WriteResult(error=f"Routine id must match filename '{agent_id}'")
        if await self._sm.get_execution_policy(graph["policy_id"]) is None:
            return WriteResult(error=f"Unknown execution policy '{graph['policy_id']}'")
        await self._sm.save_routine_graph(agent_id, graph)
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
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        agent_id, _ = resolved
        await self._sm.delete_routine_graph(agent_id)
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        graphs = await self._sm.list_routine_graphs()
        ordered = sorted(
            (graph for graph in graphs if isinstance(graph, dict) and graph.get("id")),
            key=lambda graph: (str(graph.get("name") or ""), str(graph.get("id") or "")),
        )
        entries = [
            FileInfo(path=f"/{routine_file_name(str(graph['id']))}", is_dir=False)
            for graph in ordered
        ]
        return LsResult(entries=entries)


class PolicyDirectoryBackend(RoutineDirectoryBackend):
    """Validated policy JSON files backed by the shared Postgres document store."""

    _ASYNC_ONLY = "PolicyDirectoryBackend is async-only; use the async file tools."

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("policies/")

    async def _resolve_routine(self, name: str) -> tuple[str, dict[str, Any]] | None:
        policy_id = self._parse_routine_name(name)
        if policy_id is None:
            return None
        document = await self._sm.get_execution_policy(policy_id)
        return (policy_id, document) if document is not None else None

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        from ..policies import parse_policy_document

        policy_id = self._parse_routine_name(self._name(file_path))
        if policy_id is None:
            return WriteResult(error="Expected /policies/<id>.json")
        try:
            document = parse_policy_document(content)
            if document["id"] != policy_id:
                raise ValueError("Policy id must match filename")
            await self._sm.save_execution_policy(policy_id, document)
        except ValueError as exc:
            return WriteResult(error=str(exc))
        return WriteResult(path=file_path)

    async def adelete(self, file_path: str) -> WriteResult:
        resolved = await self._resolve_routine(self._name(file_path))
        if resolved is None:
            return WriteResult(error=f"File '{file_path}' not found")
        await self._sm.delete_execution_policy(resolved[0])
        return WriteResult(path=file_path)

    async def als(self, path: str) -> LsResult:
        documents = await self._sm.list_execution_policies()
        return LsResult(
            entries=[
                FileInfo(path=f"/{doc['id']}.json", is_dir=False)
                for doc in sorted(documents, key=lambda doc: doc["id"])
            ]
        )


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
        check_entry_schema(f"entry '{entry_id}'", item, "schedule_entry")

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
    """Build the ``schedule.json`` virtual file backed by StateManager.

    ``StateManager.get_schedule()``/``save_schedule()`` already use the same
    object-keyed-by-id shape this file exposes to the agent. The presentation
    layer omits ``id`` from each value because the object key is authoritative.
    Saves diff the incoming document against the current Postgres-backed state
    and emit the same ``log_schedule_event`` audit entries as direct
    ``StateManager`` schedule mutations. There is one store read, one store
    write, and one log call per changed entry - no redundant re-reads.
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


class ContentDocumentDirectoryBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing Postgres-backed content documents as a directory.

    Backs ``/prompts/`` and ``/memories/`` (whole-kind) plus one
    ``/skills/<marketplace>__<plugin>/`` mount per installed marketplace
    plugin (``path_prefix`` scopes the backend to just that segment of the
    ``"skill"`` kind, so the mount only ever sees that plugin's own rows).
    Progenitor's own native skills are a plain disk mount now (see
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
        from .object_agent_manifest import (
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
