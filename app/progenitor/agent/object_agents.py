"""Manifest-backed internal object agents.

An object agent is defined by YAML front matter plus a Markdown system prompt in
``SYSTEM.md``. Native definitions live under ``data/agents/objects``; installed
marketplace definitions live as ``agent:`` content documents. The loader only
uses the documented manifest keys, leaving the authoring schema intentionally
more permissive for forward compatibility.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend
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
from deepagents.middleware.filesystem import FilesystemPermission
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents.middleware import InterruptOnConfig
from langchain_core.runnables import RunnableLambda

from .middleware import AuditLoggingMiddleware
from .state_backend import _AsyncOnlyFileBackendMixin

logger = logging.getLogger(__name__)

OBJECT_AGENT_KIND = "object"
OBJECT_AGENT_SYSTEM_FILE = "SYSTEM.md"
OBJECT_AGENT_DOCUMENT_KIND = "agent"
NATIVE_OBJECT_AGENT_SOURCE = "native"


@dataclass(frozen=True)
class ObjectAgentManifest:
    """Runtime-supported object-agent front matter."""

    name: str
    description: str
    version: str
    system_prompt: str
    model: str | None
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    permissions: tuple[FilesystemPermission, ...]
    interrupt_on: dict[str, bool | dict[str, Any]]


@dataclass(frozen=True)
class ObjectAgentSource:
    """One discovered agent definition and its authoritative source."""

    manifest: ObjectAgentManifest
    source: str
    relative_path: str | None = None
    native_path: Path | None = None

    @property
    def name(self) -> str:
        return self.manifest.name


def _require_string(value: Any, *, field: str, source: str) -> str:
    if not isinstance(value, str) or not (result := value.strip()):
        raise ValueError(f"{source}: '{field}' must be a non-empty string")
    return result


def _string_list(value: Any, *, field: str, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{source}: '{field}' must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _parse_permissions(value: Any, *, source: str) -> tuple[FilesystemPermission, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{source}: 'permissions' must be an array")

    result: list[FilesystemPermission] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{source}: permissions[{index}] must be an object")
        operations = entry.get("operations")
        paths = entry.get("paths")
        mode = entry.get("mode", "allow")
        if (
            not isinstance(operations, list)
            or not operations
            or any(operation not in {"read", "write"} for operation in operations)
        ):
            raise ValueError(
                f"{source}: permissions[{index}].operations must contain read and/or write"
            )
        if (
            not isinstance(paths, list)
            or not paths
            or not all(isinstance(path, str) and path for path in paths)
        ):
            raise ValueError(f"{source}: permissions[{index}].paths must be non-empty strings")
        if mode not in {"allow", "deny"}:
            raise ValueError(f"{source}: permissions[{index}].mode must be allow or deny")
        result.append(
            FilesystemPermission(
                operations=list(operations),
                paths=list(paths),
                mode=mode,
            )
        )
    return tuple(result)


def _parse_interrupt_on(value: Any, *, source: str) -> dict[str, bool | dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{source}: 'interrupt_on' must be an object keyed by tool name")

    result: dict[str, bool | dict[str, Any]] = {}
    for tool_name, config in value.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError(f"{source}: interrupt_on keys must be non-empty tool names")
        if not isinstance(config, (bool, dict)):
            raise ValueError(f"{source}: interrupt_on.{tool_name} must be a boolean or object")
        result[tool_name.strip()] = config
    return result


def parse_front_matter(content: str, *, source: str) -> dict[str, Any]:
    """Parse the raw YAML front matter of a ``SYSTEM.md`` document.

    Returns every key present, including ones the runtime manifest ignores --
    this is what lets callers (e.g. the Agents page) represent the literal
    file rather than a fixed, hardcoded field set.
    """
    if not content.startswith("---"):
        raise ValueError(f"{source}: SYSTEM.md must begin with YAML front matter")
    try:
        _, front_matter, _ = content.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"{source}: SYSTEM.md must close YAML front matter with ---") from exc
    try:
        raw = yaml.safe_load(front_matter)
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: invalid YAML front matter: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}: YAML front matter must be an object")
    return dict(raw)


def parse_object_agent_manifest(content: str, *, source: str) -> ObjectAgentManifest:
    """Parse a ``SYSTEM.md`` document without applying JSON Schema validation."""
    raw = parse_front_matter(content, source=source)
    system_prompt = content.split("---", 2)[2]
    name = _require_string(raw.get("name"), field="name", source=source)
    description = _require_string(raw.get("description"), field="description", source=source)
    version = _require_string(raw.get("version"), field="version", source=source)
    if raw.get("kind") != OBJECT_AGENT_KIND:
        raise ValueError(f"{source}: 'kind' must be '{OBJECT_AGENT_KIND}'")
    body = system_prompt.strip()
    if not body:
        raise ValueError(f"{source}: SYSTEM.md must contain a Markdown system prompt")

    model = raw.get("model")
    if model is not None:
        model = _require_string(model, field="model", source=source)
    skills = _string_list(raw.get("skills"), field="skills", source=source)
    if any(not skill.startswith("/skills/") or ".." in Path(skill).parts for skill in skills):
        raise ValueError(f"{source}: skills must be absolute paths below /skills/")

    return ObjectAgentManifest(
        name=name,
        description=description,
        version=version,
        system_prompt=body,
        model=model,
        tools=_string_list(raw.get("tools"), field="tools", source=source),
        skills=skills,
        permissions=_parse_permissions(raw.get("permissions"), source=source),
        interrupt_on=_parse_interrupt_on(raw.get("interrupt_on"), source=source),
    )


def discover_native_object_agents(root: Path) -> list[ObjectAgentSource]:
    """Discover native manifests from ``<root>/<name>/SYSTEM.md``."""
    if not root.is_dir():
        return []
    sources: list[ObjectAgentSource] = []
    for agent_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        system_path = agent_dir / OBJECT_AGENT_SYSTEM_FILE
        if not system_path.is_file():
            continue
        source = str(system_path)
        manifest = parse_object_agent_manifest(
            system_path.read_text(encoding="utf-8"), source=source
        )
        sources.append(
            ObjectAgentSource(
                manifest=manifest, source=NATIVE_OBJECT_AGENT_SOURCE, native_path=system_path
            )
        )
    return sources


async def discover_object_agents(
    state_manager: Any,
    *,
    native_root: Path,
) -> list[ObjectAgentSource]:
    """Load native and installed object-agent manifests with native precedence."""
    discovered = discover_native_object_agents(native_root)
    by_name = {source.name: source for source in discovered}
    for relative_path, content in await state_manager.list_content_documents(
        OBJECT_AGENT_DOCUMENT_KIND
    ):
        parts = relative_path.split("/")
        if len(parts) != 3 or parts[-1] != OBJECT_AGENT_SYSTEM_FILE:
            continue
        source_label = f"marketplace:{parts[0]}" if "__" in parts[0] else "user"
        try:
            manifest = parse_object_agent_manifest(content, source=f"agent:{relative_path}")
        except ValueError as exc:
            logger.warning("Skipping invalid object agent document %s: %s", relative_path, exc)
            continue
        if manifest.name in by_name:
            logger.warning(
                "Skipping object agent %r from %s because %s already defines it",
                manifest.name,
                relative_path,
                by_name[manifest.name].source,
            )
            continue
        by_name[manifest.name] = ObjectAgentSource(
            manifest=manifest,
            source=source_label,
            relative_path=relative_path,
        )
    return sorted(by_name.values(), key=lambda item: item.name)


def _tools_for_manifest(manifest: ObjectAgentManifest, all_tools: Iterable[Any]) -> list[Any]:
    available = {str(getattr(tool, "name", "")): tool for tool in all_tools}
    if not manifest.tools:
        return []
    missing = [name for name in manifest.tools if name not in available]
    if missing:
        raise ValueError(
            f"object agent {manifest.name!r} requests unknown tools: {', '.join(missing)}"
        )
    return [available[name] for name in manifest.tools]


class SelfManifestBackend(_AsyncOnlyFileBackendMixin):
    """Async backend exposing one object agent's own manifest as ``/agent/SYSTEM.md``.

    Unlike the broad, read-only ``/agents/`` introspection mount (every
    agent's manifest, for cross-agent visibility), this is a single-file,
    writable mount scoped to the agent it was built for -- so the agent can
    edit any key in its own YAML front matter, or the system-prompt body,
    and have the change take effect on its next invocation. Writes go
    through the same paths the backend UI's PATCH edit uses (disk for
    native agents, the Postgres document store for user/marketplace ones).
    A marketplace/user write's own row insert already fires the shared
    ``store`` table's Postgres NOTIFY trigger; a native write lands on disk
    only, so ``_write_raw`` always also touches a small marker document
    (``state_manager.touch_object_agent``) to trigger that same NOTIFY
    regardless of source -- one propagation mechanism, not two. The backend
    watches for both the ``agent:``-prefixed document key and the touch-marker
    key and rebroadcasts ``agents.snapshot`` on either (see
    ``ui_routes._queue_state_watch_status``).

    Renaming (which would change the derived ``agent_id`` and, for native
    agents, requires renaming a directory this mount doesn't own) is
    refused; that still has to go through the Agents page.
    """

    _ASYNC_ONLY = "SelfManifestBackend is async-only; use the async file tools."
    _FILE = "SYSTEM.md"

    def __init__(self, source: ObjectAgentSource, *, state_manager: Any) -> None:
        self._source = source
        self._sm = state_manager

    def _matches(self, file_path: str) -> bool:
        return file_path.lstrip("/") == self._FILE

    async def _read_raw(self) -> str:
        if self._source.native_path is not None:
            return self._source.native_path.read_text(encoding="utf-8")
        assert self._source.relative_path is not None
        content = await self._sm.get_content_document(
            OBJECT_AGENT_DOCUMENT_KIND, self._source.relative_path
        )
        if content is None:
            raise ValueError(f"object agent {self._source.name!r} is no longer installed")
        return content

    async def _write_raw(self, content: str) -> None:
        manifest = parse_object_agent_manifest(content, source="self-edit")
        if manifest.name != self._source.name:
            raise ValueError(
                "renaming an agent from its own SYSTEM.md is not supported; "
                "edit 'name' through the Agents page instead"
            )
        if self._source.native_path is not None:
            self._source.native_path.write_text(content, encoding="utf-8")
        else:
            assert self._source.relative_path is not None
            await self._sm.save_content_document(
                OBJECT_AGENT_DOCUMENT_KIND, self._source.relative_path, content
            )
        await self._sm.touch_object_agent(self._source.name)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        if not self._matches(file_path):
            return ReadResult(error=f"File '{file_path}' not found")
        try:
            content = await self._read_raw()
        except ValueError as exc:
            return ReadResult(error=str(exc))
        file_data = create_file_data(content)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if not self._matches(file_path):
            return WriteResult(error=f"Unknown file '{file_path}'; this mount only has SYSTEM.md")
        try:
            await self._write_raw(content)
        except ValueError as exc:
            return WriteResult(error=str(exc))
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
        return WriteResult(error="the agent's own manifest cannot be deleted, only edited")

    async def als(self, path: str) -> LsResult:
        return LsResult(entries=[FileInfo(path=f"/{self._FILE}", is_dir=False)])

    async def aglob(self, pattern: str, path: str = "/") -> GlobResult:
        return GlobResult(matches=[])

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        return GrepResult(matches=[])


SELF_MANIFEST_PROMPT_NOTE = (
    "\n\nYour own manifest is mounted read-write at /agent/SYSTEM.md -- the exact "
    "YAML front matter + Markdown body that defines you. Edit it with your file "
    "tools to change any field (description, model, tools, skills, permissions, "
    "interrupt_on, or any custom key) or your own system prompt; the change "
    "applies on your next invocation and appears live on the Agents page."
)


def create_object_agent_proxy(
    source: ObjectAgentSource,
    *,
    state_manager: Any,
    model: Any,
    tools: Iterable[Any],
    backend: Any,
    checkpointer: Any,
    agent_specs: Callable[[str], list[CompiledSubAgent]],
    log_sink: Any | None = None,
) -> CompiledSubAgent:
    """Return a lazy proxy that compiles the manifest only when invoked."""

    async def load_manifest() -> ObjectAgentManifest:
        if source.native_path is not None:
            content = source.native_path.read_text(encoding="utf-8")
            return parse_object_agent_manifest(content, source=str(source.native_path))
        assert source.relative_path is not None
        content = await state_manager.get_content_document(
            OBJECT_AGENT_DOCUMENT_KIND, source.relative_path
        )
        if content is None:
            raise ValueError(f"object agent {source.name!r} is no longer installed")
        return parse_object_agent_manifest(content, source=f"agent:{source.relative_path}")

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        manifest = await load_manifest()
        selected_tools = _tools_for_manifest(manifest, tools)
        self_routes: dict[str, Any] = {
            "/agent/": SelfManifestBackend(source, state_manager=state_manager)
        }
        self_backend = CompositeBackend(default=backend, routes=self_routes)
        agent = create_deep_agent(
            name=manifest.name,
            model=manifest.model or model,
            system_prompt=manifest.system_prompt + SELF_MANIFEST_PROMPT_NOTE,
            tools=selected_tools,
            backend=self_backend,
            skills=list(manifest.skills) or None,
            permissions=list(manifest.permissions) or None,
            interrupt_on=cast(
                "dict[str, bool | InterruptOnConfig] | None", manifest.interrupt_on or None
            ),
            checkpointer=checkpointer,
            subagents=agent_specs(manifest.name),
            middleware=[AuditLoggingMiddleware(log_sink=log_sink)],
        )
        return await agent.ainvoke({"messages": state.get("messages") or []})

    return {
        "name": source.name,
        "description": source.manifest.description,
        "runnable": RunnableLambda(run),
    }
