"""
Backend configuration for Progena's DeepAgent.

Profile:
- Ephemeral workspace (StateBackend) + persistent memories (Postgres-backed)
- Scoped filesystem mounts for self-inspection and runtime data access:
  - /memories/ -> Postgres content documents, kind="memory" (read-write)
  - /agents/user/ -> validated Postgres user agent manifests (read-write)
  - /agents/ -> other Postgres agent manifests (read-only)
  - /self/ -> <project_root>/src (read-only)
  - /repos/ -> configured repo/worktree directory (read-only)
  - /logs/ -> configured log dir (read-only)
  - /shared/ -> configured shared exchange dir (read-write), only when
    ``PROGENA_SHARED_DIR`` is set; a Docker volume other containers also mount
  - /instructions/ -> Postgres content documents, kind="instruction" (validated writes;
    bundled agent instructions read-only)
  - /skills/ -> <project_root>/data/skills (read-only, disk-mounted): progena's
    own native skills, static and baked into the image, same shape as /schemas/
  - /skills/<marketplace>__<plugin>/ -> Postgres content documents, kind="skill",
    scoped to that one installed marketplace plugin (read-only) - one such route
    per plugin segment currently installed (see plugins/install.py)
  - /schemas/ -> configured schemas dir (read-only, still disk-mounted: static
    JSON Schema contracts checked into git, read by structured_output) - a
    skill reads before calling structured_output
  - /config/ -> Postgres state documents the agent can read/edit as files,
    including mcp.json, plugins.json, marketplaces.json, schedule.json,
    agents.json, preferences.json, and profiles.json, plus the /tasks/
    task directory (board.json and per-task <id>.md files). Writes persist via
    the StateManager; the Postgres listener publishes the corresponding live UI
    snapshot.
  - /sessions/ -> chat sessions as YAML-front-matter + IRC-style markdown,
    one <date>_<title-slug>_<id8>.md per non-archived chat, backed by the
    ``session:`` documents in the store (front matter editable, body read-only)
  - /config/repos/ -> declared repositories, one <id>.json per ``repo:<id>``
    document (live git state stays in the read-only /config/repo_scan.json;
    /repos/ is the read-only checked-out source of managed repos)
  - /alerts/ -> writable alert request files (`<id>.json`); the backend stamps
    the server-managed fields and persists each as an ``alert:<id>`` document

Prompts and memories used to be plain files bind-mounted from the host (see
``content_documents.py`` for why); they're Postgres documents now. Progena's
own skills went the other way: they used to be seeded into Postgres too, but
since that content is fully static and baked into every image, they're a plain
disk mount again, same as ``schemas/``. Only *installed marketplace plugin*
skills remain Postgres-backed, since those are fetched at runtime.
"""

import logging
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from deepagents.backends.protocol import EditResult, FileUploadResponse, WriteResult

from src.progena.agent.filesystem.alerts import AlertDirectoryBackend
from src.progena.agent.filesystem.artifacts import ArtifactDirectoryBackend
from src.progena.agent.filesystem.attachments import AttachmentDirectoryBackend
from src.progena.agent.filesystem.base import (
    BoundedFilesystemBackend,
    StateDocumentBackend,
    StateFile,
)
from src.progena.agent.filesystem.canvases import CanvasDirectoryBackend
from src.progena.agent.filesystem.config import (
    create_health_state_file,
    create_indexer_state_file,
    create_knowledge_state_file,
    create_marketplaces_state_file,
    create_mcp_state_file,
    create_mcp_tools_state_file,
    create_model_profiles_state_file,
    create_plugins_state_file,
    create_preferences_state_file,
    create_registry_state_file,
    create_repo_scan_state_file,
)
from src.progena.agent.filesystem.content import (
    ContentDocumentDirectoryBackend,
    InstructionDocumentDirectoryBackend,
    UserAgentDirectoryBackend,
)
from src.progena.agent.filesystem.inbox import InboxDirectoryBackend
from src.progena.agent.filesystem.knowledge import KnowledgePageBackend
from src.progena.agent.filesystem.policies import PolicyDirectoryBackend
from src.progena.agent.filesystem.repositories import RepoDirectoryBackend
from src.progena.agent.filesystem.routines import RoutineDirectoryBackend
from src.progena.agent.filesystem.schedules import create_schedule_state_file
from src.progena.agent.filesystem.sessions import SessionDirectoryBackend
from src.progena.agent.filesystem.tasks import KanbanTaskDirectoryBackend
from src.progena.agent.filesystem.workflows import WorkflowDirectoryBackend

logger = logging.getLogger(__name__)


class ReadOnlyBackend:
    """Deny all mutation operations while allowing reads/listing."""

    def __init__(self, backend: Any):
        self._backend = backend

    async def can_write_file(self, path: str, content: str, context: dict[str, Any]) -> bool:
        return False

    async def can_delete_file(self, path: str, context: dict[str, Any]) -> bool:
        return False

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="This mount is read-only")

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return EditResult(error="This mount is read-only")

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._backend, item)


def create_object_agent_plugin_skill_routes(
    state_manager: Any, mount_paths: Iterable[str], skill_paths: Iterable[str] = ()
) -> dict[str, Any]:
    """Resolve declared plugin skill mounts without relying on startup routes.

    Each route is rooted at a declared plugin mount or a named plugin skill
    within a declared mount, rather than exposing the entire skill store.
    Native skill paths keep their disk route.
    The fresh child composite owns these routes; the parent is never mutated.
    """
    routes: dict[str, Any] = {}
    mounts = tuple(mount_paths)
    paths = set(mounts)
    for skill in skill_paths:
        path = str(PurePosixPath(skill)) + "/"
        if any(path.startswith(mount) for mount in mounts):
            paths.add(path)
    for path in sorted(paths):
        if not path.startswith("/skills/"):
            continue
        relative_path = path.removeprefix("/skills/").rstrip("/")
        segment = relative_path.split("/", 1)[0]
        if "__" not in segment:
            continue
        routes[path] = ReadOnlyBackend(
            ContentDocumentDirectoryBackend(state_manager, "skill", path_prefix=relative_path)
        )
    return routes


def create_progena_backend(
    project_root: str | Path | None = None,
    *,
    log_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    skills_dir: str | Path | None = None,
    agents_dir: str | Path | None = None,
    worktree_dir: str | Path | None = None,
    shared_dir: str | Path | None = None,
    state_manager: Any | None = None,
    database_url: str | None = None,
    checkpointer: Any | None = None,
    plugin_skill_segments: list[str] | None = None,
    plugin_agent_segments: list[str] | None = None,
) -> Any:
    """Create a backend instance for the Progena DeepAgent.

    Args:
        project_root: Root directory for filesystem access. Defaults to cwd.
        log_dir: Log directory. Defaults to ``<project_root>/logs``.
        schemas_dir: JSON Schema directory. Defaults to ``<project_root>/data/schemas``.
        skills_dir: Progena's own skill directory. Defaults to
            ``<project_root>/data/skills``. Disk-mounted read-only at ``/skills/``.
        agents_dir: Bundled agent directory, read-only at /agents/objects/
            and /agents/native/. Defaults to <project_root>/data/agents/objects.
        worktree_dir: Repository/worktree directory. Defaults to ``<project_root>/data/repos``.
        shared_dir: Cross-container exchange directory, writable at ``/shared/``.
            Omitted when unset; startup fails when set but missing.
        state_manager: Required for instructions/memories/config mounts and for
            installed marketplace-plugin skill mounts (all Postgres-backed);
            without it those routes are omitted entirely.
        database_url: Required for the ``/attachments/`` mount (raw SQL over the
            same Postgres ``attachments`` blob table the backend writes to);
            without it the mount is omitted entirely.
        checkpointer: The agent's LangGraph checkpointer. With ``state_manager``
            it enables the ``/sessions/`` chat-session mount; without either,
            that mount is omitted.
        plugin_skill_segments: One ``<marketplace>__<plugin>`` segment per
            currently-installed marketplace plugin; each gets its own
            ``/skills/<segment>/`` route scoped to just that plugin's skill
            documents. Requires ``state_manager``; ignored without one.
    Returns:
        Composite backend instance compatible with DeepAgents 0.5.3+.
    """
    root = Path(project_root) if project_root else Path.cwd()
    resolved_log_dir = Path(log_dir) if log_dir else root / "logs"
    resolved_schemas_dir = Path(schemas_dir) if schemas_dir else root / "data" / "schemas"
    resolved_skills_dir = Path(skills_dir) if skills_dir else root / "data" / "skills"
    resolved_agents_dir = Path(agents_dir) if agents_dir else root / "data" / "agents" / "objects"
    resolved_worktree_dir = Path(worktree_dir) if worktree_dir else root / "data" / "repos"

    def _route_backend(
        path: Path,
        *,
        read_only: bool,
        create_if_missing: bool = False,
    ) -> Any | None:
        if create_if_missing:
            path.mkdir(parents=True, exist_ok=True)
        elif not path.exists():
            logger.warning("Skipping backend mount for missing path: %s", path)
            return None
        backend = FilesystemBackend(root_dir=str(path), virtual_mode=True)
        if read_only:
            return ReadOnlyBackend(backend)
        return backend

    routes: dict[str, Any] = {}

    mounts: list[tuple[str, Path, bool]] = [
        ("/self/", root / "src", True),
        ("/repos/", resolved_worktree_dir, True),
        ("/logs/", resolved_log_dir, True),
        ("/schemas/", resolved_schemas_dir, True),
        ("/skills/", resolved_skills_dir, True),
        ("/agents/objects/", resolved_agents_dir, True),
        ("/agents/native/", resolved_agents_dir, True),
    ]
    for route, mount_path, ro in mounts:
        backend = _route_backend(mount_path, read_only=ro)
        if backend is not None:
            routes[route] = backend

    if shared_dir is not None:
        shared_path = Path(shared_dir)
        if not shared_path.is_dir():
            raise FileNotFoundError(f"PROGENA_SHARED_DIR is not a directory: {shared_path}")
        routes["/shared/"] = BoundedFilesystemBackend(root_dir=str(shared_path), virtual_mode=True)

    if state_manager is not None:
        routes["/agents/"] = ReadOnlyBackend(
            ContentDocumentDirectoryBackend(state_manager, "agent")
        )
        routes["/agents/user/"] = UserAgentDirectoryBackend(
            state_manager, native_root=resolved_agents_dir
        )
        routes["/memories/"] = ContentDocumentDirectoryBackend(state_manager, "memory")
        routes["/instructions/"] = InstructionDocumentDirectoryBackend(state_manager, "instruction")
        routes["/canvases/"] = CanvasDirectoryBackend(state_manager)
        routes["/artifacts/"] = ArtifactDirectoryBackend(state_manager)
        routes["/knowledge/"] = KnowledgePageBackend(state_manager)
        for segment in plugin_skill_segments or []:
            routes[f"/skills/{segment}/"] = ReadOnlyBackend(
                ContentDocumentDirectoryBackend(state_manager, "skill", path_prefix=segment)
            )
        for segment in plugin_agent_segments or []:
            routes[f"/agents/{segment}/"] = ReadOnlyBackend(
                ContentDocumentDirectoryBackend(state_manager, "agent", path_prefix=segment)
            )

        # State documents surfaced as virtual files (e.g. /config/mcp.json,
        # /config/schedule.json, /config/agents.json), backed by the runtime
        # StateManager so writes persist through the normal store path. Tasks get
        # their own mount: a directory of per-task files (/tasks/<id>.md +
        # board.json) so the agent edits one task per file instead of a giant blob.
        state_file_factories: list[Callable[[Any], StateFile]] = [
            create_mcp_state_file,
            create_mcp_tools_state_file,
            create_plugins_state_file,
            create_marketplaces_state_file,
            create_knowledge_state_file,
            create_indexer_state_file,
            create_schedule_state_file,
            create_registry_state_file,
            create_repo_scan_state_file,
            create_health_state_file,
            create_preferences_state_file,
            create_model_profiles_state_file,
        ]
        routes["/config/"] = StateDocumentBackend(
            [factory(state_manager) for factory in state_file_factories]
        )
        routes["/tasks/"] = KanbanTaskDirectoryBackend(state_manager)
        routes["/workflows/"] = WorkflowDirectoryBackend(state_manager)
        # Longest prefix wins, so this shadows /config/ for repos/ only.
        routes["/config/repos/"] = RepoDirectoryBackend(state_manager)
        routes["/alerts/"] = AlertDirectoryBackend(state_manager)
        routes["/routines/"] = RoutineDirectoryBackend(state_manager)
        routes["/policies/"] = PolicyDirectoryBackend(state_manager)
        # User inbox: a directory of per-message files (/inbox/<id>.md) the
        # agent writes to send the user rich markdown messages.
        routes["/inbox/"] = InboxDirectoryBackend(state_manager)
        routes["/sessions/"] = SessionDirectoryBackend(state_manager)

    if database_url:
        # Uploaded chat attachments, read directly from the same Postgres
        # blob table the backend writes to (see attachment_backend.py) --
        # read-only, so no state_manager dependency.
        routes["/attachments/"] = ReadOnlyBackend(AttachmentDirectoryBackend(database_url))

    state_backend_cls: Any = StateBackend
    logger.info("Created progena backend with scoped filesystem mounts")
    return CompositeBackend(
        default=state_backend_cls(),
        routes=routes,
    )
