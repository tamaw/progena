"""
Backend configuration for Progenitor's DeepAgent.

Creates a CompositeBackend that routes:
- Default: LocalShellBackend (project filesystem + shell execution)
- /memories/: StoreBackend (cross-thread persistent memory)
- /workspace/: StateBackend (ephemeral scratch space per thread)

This preserves Progenitor's existing StateManager for build/schedule state
while giving DeepAgent proper filesystem and memory backends.
"""

import logging
from pathlib import Path
from typing import Any

from deepagents.backends import (
    CompositeBackend,
    LocalShellBackend,
    StateBackend,
    StoreBackend,
)

from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


def create_progenitor_backend(
    config: ProgenitorConfig | None = None,
    project_root: str | Path | None = None,
) -> Any:
    """Create a backend factory for the Progenitor DeepAgent.

    Returns a factory callable (runtime -> BackendProtocol) suitable for
    create_deep_agent(backend=...).

    Args:
        config: Progenitor configuration.
        project_root: Root directory for filesystem access. Defaults to cwd.

    Returns:
        Backend factory callable.
    """
    root_dir = str(project_root or Path.cwd())

    def _backend_factory(runtime: Any) -> CompositeBackend:
        return CompositeBackend(
            default=LocalShellBackend(root_dir=root_dir, virtual_mode=True),
            routes={
                "/memories/": StoreBackend(runtime),
                "/workspace/": StateBackend(runtime),
            },
        )

    logger.info("Created backend factory: root_dir=%s", root_dir)
    return _backend_factory
