"""Runtime construction and invocation for manifest-backed object agents."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

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
from deepagents.middleware.filesystem import FilesystemMiddleware
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
from .backends import ReadOnlyBackend, create_object_agent_plugin_skill_routes
from .middleware import AuditLoggingMiddleware
from .object_agent_manifest import (
    OBJECT_AGENT_DOCUMENT_KIND,
    ObjectAgentManifest,
    ObjectAgentSource,
    _path_is_mounted,
    _validate_permissions_within_mounts,
    parse_object_agent_manifest,
)
from .shell_backend import ProgenitorShellBackend, ShellPaths, agent_home_slug
from .state_backend import _AsyncOnlyFileBackendMixin


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
    through the same Postgres documents the backend UI edits. Each document
    write fires the shared store's NOTIFY trigger. Bundled native manifests
    are file-backed and read-only, including through this mount.

    Renaming through this mount is refused. User and marketplace names can
    be changed through the Agents page; native source names remain fixed.
    """

    _ASYNC_ONLY = "SelfManifestBackend is async-only; use the async file tools."
    _FILE = "SYSTEM.md"

    def __init__(self, source: ObjectAgentSource, *, state_manager: Any) -> None:
        self._source = source
        self._sm = state_manager

    def _matches(self, file_path: str) -> bool:
        return file_path.lstrip("/") == self._FILE

    async def _read_raw(self) -> str:
        if self._source.file_path is not None:
            return self._source.file_path.read_text(encoding="utf-8")
        assert self._source.relative_path is not None
        content = await self._sm.get_content_document(
            OBJECT_AGENT_DOCUMENT_KIND, self._source.relative_path
        )
        if content is None:
            raise ValueError(f"object agent {self._source.name!r} is no longer installed")
        return content

    async def _write_raw(self, content: str) -> None:
        if self._source.source == "native":
            raise ValueError("native object agent manifests are read-only")
        manifest = parse_object_agent_manifest(content, source="self-edit")
        if manifest.name != self._source.name:
            raise ValueError(
                "renaming an agent from its own SYSTEM.md is not supported; "
                "edit 'name' through the Agents page instead"
            )
        assert self._source.relative_path is not None
        await self._sm.save_content_document(
            OBJECT_AGENT_DOCUMENT_KIND, self._source.relative_path, content
        )

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
    workspace_root = shell_paths.workspace_root or shell_paths.agent_home.parent / "shell-workspaces"
    workspace = workspace_root / agent_home_slug(manifest.name)

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


def _scoped_routes(
    manifest: ObjectAgentManifest, parent: Any, *, state_manager: Any | None = None
) -> dict[str, Any]:
    """Project only explicitly declared virtual mounts from the parent backend."""
    parent_routes = dict(getattr(parent, "routes", {}))
    plugin_routes: dict[str, Any] = {}
    if state_manager is not None:
        plugin_routes = create_object_agent_plugin_skill_routes(
            state_manager, (mount.path for mount in manifest.mounts), manifest.skills
        )
        parent_routes.update(plugin_routes)
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
    # Named plugin skills can be below a broad /skills/ mount. The child owns
    # the new composite, so its routing table includes these longer PG paths.
    routes.update(plugin_routes)
    return routes


def _model_for_manifest(manifest: ObjectAgentManifest, parent_model: Any) -> Any:
    """Resolve overrides through the Azure adapter, never LangChain's default client."""
    if manifest.model is None:
        return parent_model
    return create_langchain_model(replace(LLMConfig.from_env(), model_default=manifest.model))


def build_object_agent(
    manifest: ObjectAgentManifest,
    *,
    model: Any,
    tools: Iterable[Any],
    backend: Any,
    checkpointer: Any,
    state_manager: Any | None = None,
    log_sink: Any | None = None,
    worktree_dir: Path | None = None,
    shell_paths: ShellPaths | None = None,
) -> Any:
    """Build one agent from the current manifest and its parent's runtime resources.

    Callers own manifest loading and invocation. The model field remains a
    deployment override through the existing adapter, not a named profile.
    """
    selected_tools = _tools_for_manifest(manifest, tools)
    if manifest.mounts:
        _validate_permissions_within_mounts(
            manifest.permissions, manifest.mounts, source=f"object agent {manifest.name!r}"
        )
    child_model = _model_for_manifest(manifest, model)
    routes = _scoped_routes(manifest, backend, state_manager=state_manager)
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
    if manifest.skills and manifest.mounts:
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
    if manifest.mounts or workspace is not None:
        middleware.append(
            FilesystemMiddleware(
                backend=self_backend,
                _permissions=list(manifest.permissions) or None,
            )
        )
    system_prompt = manifest.system_prompt
    if workspace is not None:
        system_prompt += SHELL_PROMPT_NOTE.format(workspace=workspace)
    return create_agent(
        name=manifest.name,
        model=child_model,
        system_prompt=system_prompt,
        tools=selected_tools,
        checkpointer=checkpointer,
        middleware=middleware,
    )


def create_object_agent_proxy(
    source: ObjectAgentSource,
    *,
    state_manager: Any,
    model: Any,
    tools: Iterable[Any],
    backend: Any,
    checkpointer: Any,
    log_sink: Any | None = None,
    worktree_dir: Path | None = None,
    shell_paths: ShellPaths | None = None,
) -> CompiledSubAgent:
    """Return a lazy proxy that compiles the manifest only when invoked."""

    async def load_manifest() -> ObjectAgentManifest:
        if source.source == "native":
            # Bundled definitions are a startup snapshot; restart after dev edits.
            return source.manifest
        assert source.relative_path is not None
        content = await state_manager.get_content_document(
            OBJECT_AGENT_DOCUMENT_KIND, source.relative_path
        )
        if content is None:
            raise ValueError(f"object agent {source.name!r} is no longer installed")
        return parse_object_agent_manifest(content, source=f"agent:{source.relative_path}")

    async def run(state: dict[str, Any]) -> dict[str, Any]:
        manifest = await load_manifest()
        agent = build_object_agent(
            manifest,
            model=model,
            tools=tools,
            backend=backend,
            checkpointer=checkpointer,
            state_manager=state_manager,
            log_sink=log_sink,
            worktree_dir=worktree_dir,
            shell_paths=shell_paths,
        )
        return await agent.ainvoke({"messages": state.get("messages") or []})

    return {
        "name": source.name,
        "description": source.manifest.description,
        "runnable": RunnableLambda(run),
    }
