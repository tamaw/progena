"""
Backend configuration for Progenitor's DeepAgent.

Profile:
- Ephemeral workspace (StateBackend) + persistent memories (FilesystemBackend)
- Scoped filesystem mounts for self-inspection and runtime data access:
  - /memories/ -> configured data_dir/memories (read-write)
  - /self/ -> <project_root>/app (read-only)
  - /repos/ -> configured repo/worktree directory (read-only)
  - /data/ -> configured data dir (read-only)
  - /logs/ -> configured log dir (read-only)
  - /prompts/ -> configured prompts dir (read-only)
"""

import logging
from pathlib import Path
from typing import Any

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
)

from .state_backend import StateDocumentBackend, create_mcp_state_file

logger = logging.getLogger(__name__)


class ReadOnlyFilesystemBackend:
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
    data_dir: str | Path | None = None,
    log_dir: str | Path | None = None,
    prompts_dir: str | Path | None = None,
    worktree_dir: str | Path | None = None,
    state_manager: Any | None = None,
) -> Any:
    """Create a backend instance for the Progenitor DeepAgent.

    Args:
        project_root: Root directory for filesystem access. Defaults to cwd.
        data_dir: Persistent data directory. Defaults to ``<project_root>/data``.
        log_dir: Log directory. Defaults to ``<project_root>/logs``.
        prompts_dir: Prompt directory. Defaults to ``<data_dir>/prompts``.
        worktree_dir: Repository/worktree directory. Defaults to ``<project_root>/worktrees``.
        state_manager: When provided, mounts ``/state/`` with state documents
            (currently ``mcp.json``) the agent can read/edit as files. Writes
            persist via the StateManager; the runtime's store listener handles
            live MCP re-discovery, so no write-hook callback is needed here.
    Returns:
        Composite backend instance compatible with DeepAgents 0.5.3+.
    """
    root = Path(project_root) if project_root else Path.cwd()
    resolved_data_dir = Path(data_dir) if data_dir else root / "data"
    resolved_log_dir = Path(log_dir) if log_dir else root / "logs"
    resolved_prompts_dir = Path(prompts_dir) if prompts_dir else resolved_data_dir / "prompts"
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
            return ReadOnlyFilesystemBackend(backend)
        return backend

    routes: dict[str, Any] = {}
    memories_backend = _route_backend(
        resolved_data_dir / "memories",
        read_only=False,
        create_if_missing=True,
    )
    if memories_backend is not None:
        routes["/memories/"] = memories_backend

    mounts: list[tuple[str, Path, bool]] = [
        ("/self/", root / "app", True),
        ("/repos/", resolved_worktree_dir, True),
        ("/data/", resolved_data_dir, True),
        ("/logs/", resolved_log_dir, True),
        ("/prompts/", resolved_prompts_dir, True),
    ]
    for route, mount_path, ro in mounts:
        backend = _route_backend(mount_path, read_only=ro)
        if backend is not None:
            routes[route] = backend

    # State documents surfaced as virtual files (e.g. /state/mcp.json), backed by
    # the runtime StateManager so writes persist through the normal store path.
    if state_manager is not None:
        routes["/state/"] = StateDocumentBackend([create_mcp_state_file(state_manager)])

    state_backend_cls: Any = StateBackend
    logger.info("Created progenitor backend with scoped filesystem mounts")
    return CompositeBackend(
        default=state_backend_cls(),
        routes=routes,
    )
