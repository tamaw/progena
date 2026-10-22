"""Runtime construction and invocation for manifest-backed object agents."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, cast

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
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
    perform_string_replacement,
    slice_read_response,
)
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents import create_agent
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    SummarizationMiddleware,
)
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda

from ..provider_runtime import ProviderTurn, activate_provider_turn, current_provider_turn
from .adapters import ProviderChatModel
from .backends import ReadOnlyBackend, create_object_agent_plugin_skill_routes
from .middleware import AuditLoggingMiddleware, ToolErrorMiddleware
from .object_agent_manifest import (
    OBJECT_AGENT_DOCUMENT_KIND,
    ObjectAgentManifest,
    ObjectAgentSource,
    _validate_permissions_within_mounts,
    implicit_mounts,
    parse_object_agent_manifest,
    skill_mounts,
)
from .shell_backend import ProgenitorShellBackend, ShellPaths, agent_home_slug
from .state_backend import _AsyncOnlyFileBackendMixin

logger = logging.getLogger(__name__)


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

    def _unpath(self, path: str) -> str:
        # Results come back in the parent's coordinates; strip the suffix so
        # paths the child lists are paths it can read.
        if self._suffix and (path == self._suffix or path.startswith(f"{self._suffix}/")):
            return path[len(self._suffix) :] or "/"
        return path

    def _rebase(self, result: Any, field: str) -> Any:
        items = result if isinstance(result, list) else getattr(result, field, None)
        if not items:
            return result
        rebased = [{**item, "path": self._unpath(item["path"])} for item in items]
        return rebased if isinstance(result, list) else replace(result, **{field: rebased})

    async def aread(self, path: str, offset: int = 0, limit: int = 2000) -> Any:
        return await self._backend.aread(self._path(path), offset, limit)

    async def awrite(self, path: str, content: str) -> Any:
        return await self._backend.awrite(self._path(path), content)

    async def aedit(self, path: str, old: str, new: str, replace_all: bool = False) -> Any:
        return await self._backend.aedit(self._path(path), old, new, replace_all)

    async def adelete(self, path: str) -> Any:
        return await self._backend.adelete(self._path(path))

    async def als(self, path: str) -> Any:
        return self._rebase(await self._backend.als(self._path(path)), "entries")

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return self._rebase(await self._backend.aglob(pattern, self._path(path)), "matches")

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return self._rebase(await self._backend.agrep(pattern, self._path(path), glob), "matches")

    # Middleware (memory, skills) batch-loads through these; without them the
    # call fell through __getattr__ to the unscoped parent route.
    async def adownload_files(self, paths: list[str]) -> Any:
        responses = await self._backend.adownload_files([self._path(path) for path in paths])
        return [replace(r, path=self._unpath(r.path)) for r in responses]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        responses = await self._backend.aupload_files(
            [(self._path(path), content) for path, content in files]
        )
        return [replace(r, path=self._unpath(r.path)) for r in responses]

    # Sync and legacy forms of the same calls. Anything path-taking that fell
    # through __getattr__ would reach the whole parent route, unscoped.
    def read(self, path: str, offset: int = 0, limit: int = 2000) -> Any:
        return self._backend.read(self._path(path), offset, limit)

    def write(self, path: str, content: str) -> Any:
        return self._backend.write(self._path(path), content)

    def edit(self, path: str, old: str, new: str, replace_all: bool = False) -> Any:
        return self._backend.edit(self._path(path), old, new, replace_all)

    def ls(self, path: str) -> Any:
        return self._rebase(self._backend.ls(self._path(path)), "entries")

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return self._rebase(self._backend.glob(pattern, self._path(path)), "matches")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return self._rebase(self._backend.grep(pattern, self._path(path), glob), "matches")

    def download_files(self, paths: list[str]) -> Any:
        responses = self._backend.download_files([self._path(path) for path in paths])
        return [replace(r, path=self._unpath(r.path)) for r in responses]

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        responses = self._backend.upload_files(
            [(self._path(path), content) for path, content in files]
        )
        return [replace(r, path=self._unpath(r.path)) for r in responses]

    def ls_info(self, path: str) -> Any:
        return self._rebase(self._backend.ls_info(self._path(path)), "entries")

    async def als_info(self, path: str) -> Any:
        return self._rebase(await self._backend.als_info(self._path(path)), "entries")

    def glob_info(self, pattern: str, path: str = "/") -> Any:
        return self._rebase(self._backend.glob_info(pattern, self._path(path)), "matches")

    async def aglob_info(self, pattern: str, path: str = "/") -> Any:
        return self._rebase(await self._backend.aglob_info(pattern, self._path(path)), "matches")

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> Any:
        return self._rebase(self._backend.grep_raw(pattern, self._path(path), glob), "matches")

    async def agrep_raw(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> Any:
        raw = await self._backend.agrep_raw(pattern, self._path(path), glob)
        return self._rebase(raw, "matches")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


def _without_base_permissions(
    permissions: Iterable[FilesystemPermission], bases: Iterable[str]
) -> tuple[FilesystemPermission, ...]:
    """``permissions`` minus paths inside ``/knowledge/<base>/`` for each skipped base."""
    prefixes = tuple(f"/knowledge/{base}/" for base in bases)
    kept: list[FilesystemPermission] = []
    for rule in permissions:
        paths = [path for path in rule.paths if not f"{path.rstrip('/')}/".startswith(prefixes)]
        if len(paths) == len(rule.paths):
            kept.append(rule)
        elif paths:
            kept.append(
                FilesystemPermission(operations=rule.operations, paths=paths, mode=rule.mode)
            )
    return tuple(kept)


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
        manifest = parse_object_agent_manifest(
            content, source="self-edit", source_kind=self._source.source
        )
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
    "them directly in shell commands. Declared repos with a relative "
    "`local_path` live under `$PROGENITOR_REPOS_DIR`. {git_access} "
    "Report what the commands actually returned; never claim a repository state "
    "you did not observe."
)
GIT_READ_ONLY_NOTE = (
    "`git` and `gh` are available but wrapped: read-only subcommands succeed, "
    "mutating ones are refused with exit code 2."
)
GIT_WRITE_NOTE = (
    "`git` and `gh` are available but wrapped: read-only subcommands plus "
    "repository setup succeed (clone, fetch, pull --ff-only, switch, worktree "
    "add/remove, submodule, branch create, remote add/set-url, gh repo clone); push, "
    "commit and history-rewriting or work-discarding commands (reset, rebase, merge, "
    "clean, rm, checkout, forced switch or worktree remove, branch delete) are "
    "refused with exit code 2."
)
GIT_PUBLISH_NOTE = (
    "`git` and `gh` are available but wrapped: read-only subcommands plus "
    "repository setup succeed (clone, fetch, pull --ff-only, switch, worktree "
    "add/remove, submodule, branch create, remote add/set-url, gh repo clone), and "
    "you may publish: `git push [-u] <remote> <branch>...` for named branches other "
    "than main/master (never forced, no refspecs) and `gh pr create`. GitHub "
    "authentication is automatic. Commit and history-rewriting or work-discarding "
    "commands (reset, rebase, merge, clean, rm, checkout, forced switch or worktree "
    "remove, branch delete) are refused with exit code 2."
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
    workspace_root = (
        shell_paths.workspace_root or shell_paths.agent_home.parent / "shell-workspaces"
    )
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
                repos_dir=worktree_dir,
                home_lifecycle=shell_paths.home_lifecycle,
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
    """Apply a manifest override to the current persisted-provider connection."""
    if manifest.model is None:
        return parent_model
    if isinstance(parent_model, ProviderChatModel):
        return parent_model.with_model_override(manifest.model)
    return ProviderChatModel(model_override=manifest.model)


AGENT_MEMORY_FILE = "AGENTS.md"
# What a memory: true agent sees; backed by its private documents only.
AGENT_MEMORY_MOUNT = "/memories/"
CURATING_KNOWLEDGE_SKILL = "/skills/curating-knowledge"


def object_agent_memory_prefix(name: str) -> str:
    """Storage prefix (memory document kind) of one agent's private memory."""
    return f"agents/{agent_home_slug(name)}/"


async def object_agent_memory_sources(state_manager: Any, name: str) -> list[str]:
    """Every file in the agent's private memory, AGENTS.md first (as Progenitor
    loads all of its own memory documents, not one fixed file)."""
    prefix = object_agent_memory_prefix(name)
    rows = await state_manager.list_content_documents("memory")
    files = sorted(path.removeprefix(prefix) for path, _ in rows if path.startswith(prefix))
    return [
        f"{AGENT_MEMORY_MOUNT}{AGENT_MEMORY_FILE}",
        *(f"{AGENT_MEMORY_MOUNT}{file}" for file in files if file != AGENT_MEMORY_FILE),
    ]


class _DeclaredSkillsBackend(BackendProtocol):
    """Show SkillsMiddleware only the skills the manifest lists.

    Its loader lists a source folder and loads ``<child>/SKILL.md`` for every
    sub-folder. Listing each declared skill's parent here returns just the
    declared skills, never their siblings; file reads go to the agent's backend.
    """

    def __init__(self, backend: Any, skill_dirs: list[str], *, agent_name: str) -> None:
        self._backend = backend
        self._skill_dirs = skill_dirs
        self._agent_name = agent_name

    async def als(self, path: str) -> LsResult:
        parent = PurePosixPath(path)
        return LsResult(
            entries=[
                FileInfo(path=skill_dir, is_dir=True)
                for skill_dir in self._skill_dirs
                if PurePosixPath(skill_dir).parent == parent
            ]
        )

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses = await self._backend.adownload_files(paths)
        for path, response in zip(paths, responses, strict=True):
            if response.error is not None:
                # Otherwise a typo or a folder of skills just loads nothing.
                logger.warning(
                    "object agent %r: listed skill %s not loaded (%s); skills entries "
                    "must name a folder containing SKILL.md",
                    self._agent_name,
                    path.removesuffix("/SKILL.md"),
                    response.error,
                )
        return responses


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
    memory_sources: list[str] | None = None,
) -> Any:
    """Build one agent from the current manifest and its parent's runtime resources.

    Callers own manifest loading and invocation. The model field remains a
    deployment override through the existing adapter, not a named profile.
    """
    if manifest.knowledge and CURATING_KNOWLEDGE_SKILL not in manifest.skills:
        manifest = replace(manifest, skills=manifest.skills + (CURATING_KNOWLEDGE_SKILL,))
    # The parent's `search` covers every base; an object agent only ever gets it
    # scoped to its knowledge grants, and not at all without grants.
    selected_tools = [
        tool
        for tool in _tools_for_manifest(manifest, tools)
        if getattr(tool, "name", "") != "search"
    ]
    if manifest.knowledge:
        from ..tools.search import SearchTool
        from .adapters import wrap_system_tool

        granted = frozenset(grant.base for grant in manifest.knowledge)
        selected_tools.append(
            wrap_system_tool(SearchTool(allowed_bases=granted), state_manager=state_manager)
        )
    if manifest.mounts or manifest.knowledge:
        _validate_permissions_within_mounts(
            manifest.permissions,
            (
                *manifest.mounts,
                *implicit_mounts(
                    manifest.skills, memory=manifest.memory, knowledge=manifest.knowledge
                ),
            ),
            source=f"object agent {manifest.name!r}",
        )
    child_model = _model_for_manifest(manifest, model)
    skill_dirs = [mount.path for mount in skill_mounts(manifest.skills)]
    # Listed skills are read mounts of their own folders (a declared mount of the
    # same path keeps its access).
    declared = {mount.path for mount in manifest.mounts}
    extra = [m for m in skill_mounts(manifest.skills) if m.path not in declared]
    if extra:
        manifest = replace(manifest, mounts=manifest.mounts + tuple(extra))
    routes = _scoped_routes(manifest, backend, state_manager=state_manager)
    if manifest.memory:
        # The agent's /memories/ is its own private folder, always writable.
        parent_memories = dict(getattr(backend, "routes", {})).get(AGENT_MEMORY_MOUNT)
        if parent_memories is None:
            raise ValueError(
                f"object agent {manifest.name!r} enables memory but the runtime has no "
                f"{AGENT_MEMORY_MOUNT} route"
            )
        routes[AGENT_MEMORY_MOUNT] = ScopedMountBackend(
            parent_memories, "/" + object_agent_memory_prefix(manifest.name)
        )
    if manifest.knowledge:
        parent_knowledge = dict(getattr(backend, "routes", {})).get("/knowledge/")
        if parent_knowledge is None:
            raise ValueError(
                f"object agent {manifest.name!r} declares knowledge but the runtime has no "
                "/knowledge/ route"
            )
        for grant in manifest.knowledge:
            scoped = ScopedMountBackend(parent_knowledge, f"/{grant.base}")
            routes[f"/knowledge/{grant.base}/"] = (
                scoped if grant.access == "write" else ReadOnlyBackend(scoped)
            )
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
        ToolErrorMiddleware(),
        SummarizationMiddleware(model=child_model),
        AuditLoggingMiddleware(log_sink=log_sink),
    ]
    if skill_dirs:
        middleware.append(
            SkillsMiddleware(
                backend=_DeclaredSkillsBackend(self_backend, skill_dirs, agent_name=manifest.name),
                sources=sorted({f"{PurePosixPath(d).parent}/" for d in skill_dirs}),
            )
        )
    if manifest.memory:
        middleware.append(
            MemoryMiddleware(
                backend=self_backend,
                sources=memory_sources or [f"{AGENT_MEMORY_MOUNT}{AGENT_MEMORY_FILE}"],
            )
        )
    if manifest.interrupt_on:
        middleware.append(
            HumanInTheLoopMiddleware(
                cast("dict[str, bool | InterruptOnConfig]", manifest.interrupt_on)
            )
        )
    if manifest.mounts or manifest.memory or manifest.knowledge or workspace is not None:
        middleware.append(
            FilesystemMiddleware(
                backend=self_backend,
                _permissions=list(manifest.permissions) or None,
            )
        )
    system_prompt = manifest.system_prompt
    if workspace is not None:
        shell = manifest.shell
        git_access = (
            GIT_PUBLISH_NOTE
            if shell is not None and shell.git_publish
            else GIT_WRITE_NOTE
            if shell is not None and shell.git_write
            else GIT_READ_ONLY_NOTE
        )
        system_prompt += SHELL_PROMPT_NOTE.format(workspace=workspace, git_access=git_access)
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
        return parse_object_agent_manifest(
            content, source=f"agent:{source.relative_path}", source_kind=source.source
        )

    async def run_manifest(
        state: Any, config: RunnableConfig, manifest: ObjectAgentManifest
    ) -> dict[str, Any]:
        warnings: list[str] = []
        if manifest.knowledge:
            catalog = await state_manager.get_knowledge_config()
            catalog_ids = {
                str(base.get("id"))
                for base in catalog.get("bases", [])
                if isinstance(base, dict) and base.get("id")
            }
            known = []
            skipped = []
            for grant in manifest.knowledge:
                if grant.base in catalog_ids:
                    known.append(grant)
                else:
                    skipped.append(grant.base)
                    warning = (
                        f"Manifest for '{manifest.name}' grants knowledge base '{grant.base}', "
                        "which does not exist in /config/knowledge.json; access was skipped. "
                        "Fix the agent's SYSTEM.md 'knowledge' list or create the base."
                    )
                    warnings.append(warning)
                    logger.warning(warning)
            if skipped:
                # Permissions for a skipped base would point outside the agent's
                # mounts and fail the build; drop just those paths.
                manifest = replace(
                    manifest,
                    knowledge=tuple(known),
                    permissions=_without_base_permissions(manifest.permissions, skipped),
                )
        profile = config.get("configurable", {}).get("model_profile")
        selected_model = model
        if profile:
            from ..state.model_profiles import resolve_model_profile

            selected_model = await resolve_model_profile(state_manager, profile)
            manifest = replace(manifest, model=None)
        memory_sources = (
            await object_agent_memory_sources(state_manager, manifest.name)
            if manifest.memory
            else None
        )
        agent = build_object_agent(
            manifest,
            model=selected_model,
            tools=tools,
            backend=backend,
            checkpointer=checkpointer,
            state_manager=state_manager,
            log_sink=log_sink,
            worktree_dir=worktree_dir,
            shell_paths=shell_paths,
            memory_sources=memory_sources,
        )
        thread_id = config.get("configurable", {}).get("object_agent_thread_id")

        async def invoke() -> dict[str, Any]:
            if thread_id:
                return await agent.ainvoke(state, config={"configurable": {"thread_id": thread_id}})
            return await agent.ainvoke({"messages": state.get("messages") or []})

        if current_provider_turn() is not None or not isinstance(selected_model, ProviderChatModel):
            result = await invoke()
        else:
            session_id = str(
                thread_id
                or config.get("configurable", {}).get("thread_id")
                or f"object-agent:{manifest.name}"
            )
            turn = await ProviderTurn.create(
                session_id=session_id,
                provider_id=selected_model.provider_id,
            )
            with activate_provider_turn(turn):
                result = await invoke()
        if warnings and isinstance(result, dict):
            messages = list(result.get("messages") or [])
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                if isinstance(message, AIMessage) and message.text:
                    messages.append(
                        AIMessage(
                            content=f"{message.text}\n\nWarnings:\n- " + "\n- ".join(warnings)
                        )
                    )
                    result["messages"] = messages
                    break
        return result

    async def run(state: Any, config: RunnableConfig) -> dict[str, Any]:
        manifest = await load_manifest()
        homes = shell_paths.home_lifecycle if shell_paths is not None else None
        with homes.use(manifest.name) if homes is not None else nullcontext():
            return await run_manifest(state, config, manifest)

    return {
        "name": source.name,
        "description": source.manifest.description,
        "runnable": RunnableLambda(run),
    }
