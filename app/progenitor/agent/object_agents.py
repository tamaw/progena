"""Manifest-backed internal object agents.

An object agent is defined by YAML front matter plus a Markdown system prompt in
``SYSTEM.md``. Native definitions live under ``data/agents/objects``; installed
marketplace definitions live as ``agent:`` content documents. The loader only
uses the documented manifest keys, leaving the authoring schema intentionally
more permissive for forward compatibility.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

import yaml
from deepagents.backends import CompositeBackend, StateBackend
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
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    SummarizationMiddleware,
)
from langchain_core.runnables import RunnableLambda

from ..config import LLMConfig
from .adapters import create_langchain_model
from .backends import ReadOnlyBackend
from .middleware import AuditLoggingMiddleware
from .shell_backend import ProgenitorShellBackend, ShellPaths, agent_home_slug
from .state_backend import _AsyncOnlyFileBackendMixin

logger = logging.getLogger(__name__)

OBJECT_AGENT_KIND = "object"
OBJECT_AGENT_SYSTEM_FILE = "SYSTEM.md"
OBJECT_AGENT_DOCUMENT_KIND = "agent"
NATIVE_OBJECT_AGENT_SOURCE = "native"


DEFAULT_SHELL_MAX_OUTPUT_BYTES: Final = 100_000
DEFAULT_SHELL_SYSTEM_PATH: Final = "/usr/bin:/bin"


@dataclass(frozen=True)
class ObjectAgentShellConfig:
    """Runtime shell settings parsed from flat manifest front matter."""

    max_output_bytes: int
    env_passthrough: tuple[str, ...]
    system_path: str


@dataclass(frozen=True)
class ObjectAgentMount:
    """A virtual route exposed to a manifest-backed object agent."""

    path: str
    access: str


class ScopedMountBackend:
    """Rebase one child route onto a subdirectory of a parent route.

    ``CompositeBackend`` strips its route before delegating.  Re-applying the
    suffix here is what makes ``/repos/project-a/`` a real boundary rather
    than an alias for all of ``/repos/``.
    """

    def __init__(self, backend: Any, suffix: str) -> None:
        self._backend = backend
        self._suffix = suffix.rstrip("/")

    def _path(self, path: str | None) -> str | None:
        if path is None:
            return None
        return f"{self._suffix}/{path.lstrip('/')}".rstrip("/") or "/"

    async def aread(self, path: str, offset: int = 0, limit: int = 2000) -> Any:
        return await self._backend.aread(self._path(path), offset, limit)

    async def awrite(self, path: str, content: str) -> Any:
        return await self._backend.awrite(self._path(path), content)

    async def aedit(self, path: str, old: str, new: str, replace_all: bool = False) -> Any:
        return await self._backend.aedit(self._path(path), old, new, replace_all)

    async def adelete(self, path: str) -> Any:
        return await self._backend.adelete(self._path(path))

    async def als(self, path: str) -> Any:
        return await self._backend.als(self._path(path))

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return await self._backend.aglob(pattern, self._path(path))

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return await self._backend.agrep(pattern, self._path(path), glob)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


_SHELL_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


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
    mounts: tuple[ObjectAgentMount, ...]
    interrupt_on: dict[str, bool | dict[str, Any]]
    shell: ObjectAgentShellConfig | None = None


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


def _canonical_virtual_path(value: Any, *, field: str, source: str) -> str:
    """Validate a virtual backend path, never a host filesystem path."""
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError(f"{source}: '{field}' must be an absolute virtual path")
    path = PurePosixPath(value)
    if ".." in path.parts or str(path) != value.rstrip("/") and value != "/":
        raise ValueError(f"{source}: '{field}' must be a canonical virtual path without traversal")
    # A mount names a directory route.  Normalise its trailing slash so route
    # containment is unambiguous, while permissions may name files or dirs.
    return str(path) + ("/" if field.startswith("mounts") and str(path) != "/" else "")


def _parse_mounts(value: Any, *, source: str) -> tuple[ObjectAgentMount, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{source}: 'mounts' must be an array")
    mounts: list[ObjectAgentMount] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{source}: mounts[{index}] must be an object")
        path = _canonical_virtual_path(entry.get("path"), field=f"mounts[{index}].path", source=source)
        access = entry.get("access")
        if access not in {"read", "read-write"}:
            raise ValueError(f"{source}: mounts[{index}].access must be read or read-write")
        mounts.append(ObjectAgentMount(path=path, access=access))
    if len({mount.path for mount in mounts}) != len(mounts):
        raise ValueError(f"{source}: mounts must not contain duplicate paths")
    return tuple(mounts)


def _path_is_mounted(path: str, mounts: Iterable[ObjectAgentMount]) -> bool:
    normalized = _canonical_virtual_path(path, field="permissions.paths", source="manifest")
    return any(normalized.startswith(mount.path) or normalized == mount.path.rstrip("/") for mount in mounts)


def _validate_permissions_within_mounts(
    permissions: Iterable[FilesystemPermission], mounts: Iterable[ObjectAgentMount], *, source: str
) -> None:
    mount_list = tuple(mounts)
    for rule in permissions:
        for path in rule.paths:
            if not _path_is_mounted(path, mount_list):
                raise ValueError(
                    f"{source}: permissions path {path!r} is outside declared mounts"
                )


def _parse_shell(
    value: Any,
    *,
    env_passthrough_value: Any,
    has_env_passthrough: bool,
    source: str,
) -> ObjectAgentShellConfig | None:
    if value is None:
        enabled = False
    elif isinstance(value, bool):
        enabled = value
    else:
        raise ValueError(f"{source}: 'shell' must be a boolean")

    env_passthrough = _string_list(env_passthrough_value, field="env_passthrough", source=source)
    if any(_SHELL_ENV_NAME.fullmatch(name) is None for name in env_passthrough):
        raise ValueError(
            f"{source}: env_passthrough entries must be upper-case environment variable names"
        )
    if not enabled:
        if has_env_passthrough:
            raise ValueError(f"{source}: env_passthrough requires shell: true")
        return None

    return ObjectAgentShellConfig(
        max_output_bytes=DEFAULT_SHELL_MAX_OUTPUT_BYTES,
        env_passthrough=env_passthrough,
        system_path=DEFAULT_SHELL_SYSTEM_PATH,
    )


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

    mounts = _parse_mounts(raw.get("mounts"), source=source)
    permissions = _parse_permissions(raw.get("permissions"), source=source)
    # Manifests written before scoped mounts existed remain valid.  They have
    # no filesystem surface now; once a manifest declares mounts every
    # operation-level permission must be contained by one of them.
    if mounts:
        _validate_permissions_within_mounts(permissions, mounts, source=source)

    return ObjectAgentManifest(
        name=name,
        description=description,
        version=version,
        system_prompt=body,
        model=model,
        tools=_string_list(raw.get("tools"), field="tools", source=source),
        skills=skills,
        permissions=permissions,
        mounts=mounts,
        interrupt_on=_parse_interrupt_on(raw.get("interrupt_on"), source=source),
        shell=_parse_shell(  # nosec B604 - `shell` here is the ObjectAgentManifest field name, not subprocess.run's shell= kwarg
            raw.get("shell"),
            env_passthrough_value=raw.get("env_passthrough"),
            has_env_passthrough="env_passthrough" in raw,
            source=source,
        ),
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


class SelfManifestBackend(_AsyncOnlyFileBackendMixin):  # pragma: no cover
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


SHELL_PROMPT_NOTE = (
    "\n\nYou have an `execute` tool running a real shell with its working "
    "directory at {workspace}. Paths from the file tools are host paths — use "
    "them directly in shell commands. `git` and `gh` are available but wrapped: "
    "read-only subcommands succeed, mutating ones are refused with exit code 2. "
    "Report what the commands actually returned; never claim a repository state "
    "you did not observe."
)


def _build_shell_backend(
    manifest: ObjectAgentManifest,
    *,
    worktree_dir: Path | None,
    shell_paths: ShellPaths | None,
) -> tuple[CompositeBackend, Path]:
    """Build an isolated shell default; routes are added by the scoped builder."""
    config = manifest.shell
    assert config is not None
    if shell_paths is None:
        raise ValueError(
            f"object agent {manifest.name!r} enables shell but the runtime supplied no shell paths"
        )

    if worktree_dir is None:
        raise ValueError(
            f"object agent {manifest.name!r} enables shell but the runtime has no "
            "PROGENITOR_WORKTREE_DIR configured"
        )
    workspace = worktree_dir / ".object-agent-workspaces" / agent_home_slug(manifest.name)

    return (
        CompositeBackend(
            default=ProgenitorShellBackend(
                workspace=workspace,
                timeout=shell_paths.timeout_seconds,
                config=config,
                shim_bin=shell_paths.shim_bin,
                agent_home=shell_paths.agent_home / agent_home_slug(manifest.name),
                parent_env=os.environ,
            ),
            routes={},
        ),
        workspace,
    )


def _scoped_routes(manifest: ObjectAgentManifest, parent: Any) -> dict[str, Any]:
    """Project only explicitly declared virtual mounts from the parent backend."""
    parent_routes = dict(getattr(parent, "routes", {}))
    routes: dict[str, Any] = {}
    for mount in manifest.mounts:
        candidates = [route for route in parent_routes if mount.path.startswith(route)]
        if not candidates:
            raise ValueError(
                f"object agent {manifest.name!r}: mount {mount.path!r} is not a known parent route"
            )
        parent_route = max(candidates, key=len)
        suffix = "/" + mount.path[len(parent_route) :].strip("/")
        mounted_backend = ScopedMountBackend(parent_routes[parent_route], suffix)
        routes[mount.path] = (
            mounted_backend if mount.access == "read-write" else ReadOnlyBackend(mounted_backend)
        )
    return routes


def _model_for_manifest(manifest: ObjectAgentManifest, parent_model: Any) -> Any:
    """Resolve overrides through the Azure adapter, never LangChain's default client."""
    if manifest.model is None:
        return parent_model
    return create_langchain_model(replace(LLMConfig.from_env(), model_default=manifest.model))


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
    worktree_dir: Path | None = None,
    shell_paths: ShellPaths | None = None,
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
        _validate_permissions_within_mounts(
            manifest.permissions, manifest.mounts, source=f"object agent {manifest.name!r}"
        )
        child_model = _model_for_manifest(manifest, model)
        routes = _scoped_routes(manifest, backend)
        if manifest.shell is None:
            self_backend = CompositeBackend(default=StateBackend(), routes=routes)
            workspace = None
        else:
            self_backend, workspace = _build_shell_backend(
                manifest,
                worktree_dir=worktree_dir,
                shell_paths=shell_paths,
            )
            self_backend = CompositeBackend(default=self_backend.default, routes=routes)

        middleware: list[Any] = [
            SummarizationMiddleware(model=child_model),
            AuditLoggingMiddleware(log_sink=log_sink),
        ]
        if manifest.skills:
            if not all(_path_is_mounted(skill, manifest.mounts) for skill in manifest.skills):
                raise ValueError(
                    f"object agent {manifest.name!r}: skills must be covered by declared mounts"
                )
            middleware.append(SkillsMiddleware(backend=self_backend, sources=list(manifest.skills)))
        if manifest.interrupt_on:
            middleware.append(
                HumanInTheLoopMiddleware(
                    cast("dict[str, bool | InterruptOnConfig]", manifest.interrupt_on)
                )
            )
        if manifest.mounts:
            middleware.append(
                FilesystemMiddleware(
                    backend=self_backend,
                    _permissions=list(manifest.permissions) or None,
                )
            )
        system_prompt = manifest.system_prompt
        if workspace is not None:
            system_prompt += SHELL_PROMPT_NOTE.format(workspace=workspace)
        agent = create_agent(
            name=manifest.name,
            model=child_model,
            system_prompt=system_prompt,
            tools=selected_tools,
            checkpointer=checkpointer,
            middleware=middleware,
        )
        return await agent.ainvoke({"messages": state.get("messages") or []})

    return {
        "name": source.name,
        "description": source.manifest.description,
        "runnable": RunnableLambda(run),
    }
