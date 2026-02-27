"""
Backend configuration for Progenitor's DeepAgent.

Profile:
- Ephemeral workspace (StateBackend) + cross-thread memory (StoreBackend)
- Scoped filesystem mounts for self-inspection and runtime data access:
  - /self/ -> <project_root>/app (read-only)
  - /data/ -> <project_root>/data (read-only)
  - /logs/ -> <project_root>/logs (read-only)
  - /prompts/ -> <project_root>/prompts (read-only)
"""

import logging
from pathlib import Path
from typing import Any

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
    StoreBackend,
)

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
) -> Any:
    """Create a backend factory for the Progenitor DeepAgent.

    Returns a factory callable (runtime -> BackendProtocol) suitable for
    create_deep_agent(backend=...).

    Args:
        project_root: Root directory for filesystem access. Defaults to cwd.

    Returns:
        Backend factory callable.
    """
    root = Path(project_root) if project_root else Path.cwd()

    def _route_backend(path: Path, *, read_only: bool) -> Any | None:
        if not path.exists():
            logger.warning("Skipping backend mount for missing path: %s", path)
            return None
        backend = FilesystemBackend(root_dir=str(path), virtual_mode=True)
        if read_only:
            return ReadOnlyFilesystemBackend(backend)
        return backend

    def _backend_factory(runtime: Any) -> CompositeBackend:
        routes: dict[str, Any] = {
            "/memories/": StoreBackend(runtime),
        }

        mounts: list[tuple[str, Path, bool]] = [
            ("/self/", root / "app", True),
            ("/data/", root / "data", True),
            ("/logs/", root / "logs", True),
            ("/prompts/", root / "prompts", True),
        ]
        for route, mount_path, ro in mounts:
            backend = _route_backend(mount_path, read_only=ro)
            if backend is not None:
                routes[route] = backend

        return CompositeBackend(
            default=StateBackend(runtime),
            routes=routes,
        )

    logger.info("Created progenitor backend factory with scoped filesystem mounts")
    return _backend_factory
