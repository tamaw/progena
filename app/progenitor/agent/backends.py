"""
Backend configuration for Progenitor's DeepAgent.

Profile:
- Ephemeral workspace (StateBackend) + persistent memories (Postgres-backed)
- Scoped filesystem mounts for self-inspection and runtime data access:
  - /memories/ -> Postgres content documents, kind="memory" (read-write)
  - /self/ -> <project_root>/app (read-only)
  - /repos/ -> configured repo/worktree directory (read-only)
  - /logs/ -> configured log dir (read-only)
  - /prompts/ -> Postgres content documents, kind="prompt" (read-only)
  - /skills/ -> <project_root>/data/skills (read-only, disk-mounted): progenitor's
    own native skills, static and baked into the image, same shape as /schemas/
  - /skills/<marketplace>__<plugin>/ -> Postgres content documents, kind="skill",
    scoped to that one installed marketplace plugin (read-only) - one such route
    per plugin segment currently installed (see plugins/install.py)
  - /schemas/ -> configured schemas dir (read-only, still disk-mounted: static
    JSON Schema contracts checked into git, read by structured_output) - a
    skill reads before calling structured_output
  - /config/ -> Postgres state documents the agent can read/edit as files,
    including mcp.json, plugins.json, marketplaces.json, schedule.json,
    agents.json, and preferences.json, plus the /config/tasks/ task directory
    (board.json and per-task <id>.md files).
    Writes persist via the StateManager; the runtime's store listener handles
    live MCP re-discovery, so no write-hook callback is needed here.

Prompts and memories used to be plain files bind-mounted from the host (see
``content_documents.py`` for why); they're Postgres documents now. Progenitor's
own skills went the other way: they used to be seeded into Postgres too, but
since that content is fully static and baked into every image, they're a plain
disk mount again, same as ``schemas/``. Only *installed marketplace plugin*
skills remain Postgres-backed, since those are fetched at runtime.
"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
)

from .attachment_backend import AttachmentDirectoryBackend
from .state_backend import (
    ContentDocumentDirectoryBackend,
    InboxDirectoryBackend,
    KanbanTaskDirectoryBackend,
    StateDocumentBackend,
    StateFile,
    WorkflowDirectoryBackend,
    create_health_state_file,
    create_knowledge_state_file,
    create_marketplaces_state_file,
    create_mcp_state_file,
    create_plugins_state_file,
    create_preferences_state_file,
    create_registry_state_file,
    create_schedule_state_file,
)

logger = logging.getLogger(__name__)


class ReadOnlyBackend:
    """Deny all mutation operations while allowing reads/listing."""

    def __init__(self, backend: Any):
        self._backend = backend

    async def can_write_file(self, path: str, content: str, context: dict[str, Any]) -> bool:
        return False

    async def can_delete_file(self, path: str, context: dict[str, Any]) -> bool:
        return False

    def __getattr__(self, item: str) -> Any:
        return getattr(self._backend, item)


def create_progenitor_backend(
    project_root: str | Path | None = None,
    *,
    log_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    skills_dir: str | Path | None = None,
    worktree_dir: str | Path | None = None,
    state_manager: Any | None = None,
    database_url: str | None = None,
    plugin_skill_segments: list[str] | None = None,
) -> Any:
    """Create a backend instance for the Progenitor DeepAgent.

    Args:
        project_root: Root directory for filesystem access. Defaults to cwd.
        log_dir: Log directory. Defaults to ``<project_root>/logs``.
        schemas_dir: JSON Schema directory. Defaults to ``<project_root>/data/schemas``.
        skills_dir: Progenitor's own skill directory. Defaults to
            ``<project_root>/data/skills``. Disk-mounted read-only at ``/skills/``.
        worktree_dir: Repository/worktree directory. Defaults to ``<project_root>/worktrees``.
        state_manager: Required for prompts/memories/config mounts and for
            installed marketplace-plugin skill mounts (all Postgres-backed);
            without it those routes are omitted entirely.
        database_url: Required for the ``/attachments/`` mount (raw SQL over the
            same Postgres ``attachments`` blob table the backend writes to);
            without it the mount is omitted entirely.
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
    resolved_worktree_dir = Path(worktree_dir) if worktree_dir else root / "worktrees"

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
        ("/self/", root / "app", True),
        ("/repos/", resolved_worktree_dir, True),
        ("/logs/", resolved_log_dir, True),
        ("/schemas/", resolved_schemas_dir, True),
        ("/skills/", resolved_skills_dir, True),
    ]
    for route, mount_path, ro in mounts:
        backend = _route_backend(mount_path, read_only=ro)
        if backend is not None:
            routes[route] = backend

    if state_manager is not None:
        routes["/memories/"] = ContentDocumentDirectoryBackend(state_manager, "memory")
        routes["/prompts/"] = ReadOnlyBackend(
            ContentDocumentDirectoryBackend(state_manager, "prompt")
        )
        for segment in plugin_skill_segments or []:
            routes[f"/skills/{segment}/"] = ReadOnlyBackend(
                ContentDocumentDirectoryBackend(state_manager, "skill", path_prefix=segment)
            )

        # State documents surfaced as virtual files (e.g. /config/mcp.json,
        # /config/schedule.json, /config/agents.json), backed by the runtime
        # StateManager so writes persist through the normal store path. Tasks get
        # their own mount: a directory of per-task files (/config/tasks/<id>.md +
        # board.json) so the agent edits one task per file instead of a giant blob.
        state_file_factories: list[Callable[[Any], StateFile]] = [
            create_mcp_state_file,
            create_plugins_state_file,
            create_marketplaces_state_file,
            create_knowledge_state_file,
            create_schedule_state_file,
            create_registry_state_file,
            create_health_state_file,
            create_preferences_state_file,
        ]
        routes["/config/"] = StateDocumentBackend(
            [factory(state_manager) for factory in state_file_factories]
        )
        routes["/config/tasks/"] = KanbanTaskDirectoryBackend(state_manager)
        routes["/workflows/"] = WorkflowDirectoryBackend(state_manager)
        # User inbox: a directory of per-message files (/inbox/<id>.md) the
        # agent writes to send the user rich markdown messages.
        routes["/inbox/"] = InboxDirectoryBackend(state_manager)

    if database_url:
        # Uploaded chat attachments, read directly from the same Postgres
        # blob table the backend writes to (see attachment_backend.py) --
        # read-only, so no state_manager dependency.
        routes["/attachments/"] = ReadOnlyBackend(AttachmentDirectoryBackend(database_url))

    state_backend_cls: Any = StateBackend
    logger.info("Created progenitor backend with scoped filesystem mounts")
    return CompositeBackend(
        default=state_backend_cls(),
        routes=routes,
    )
