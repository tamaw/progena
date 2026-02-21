"""
Backend configuration for Progenitor's DeepAgent.

Core-only profile:
- No project filesystem access
- No shell execution
- Only ephemeral workspace (StateBackend) and cross-thread memory (StoreBackend)
"""

import logging
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

logger = logging.getLogger(__name__)


def create_progenitor_backend(
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
    # project_root intentionally unused in core-only mode.
    def _backend_factory(runtime: Any) -> CompositeBackend:
        return CompositeBackend(
            default=StateBackend(runtime),
            routes={
                "/memories/": StoreBackend(runtime),
            },
        )

    logger.info("Created core-only backend factory")
    return _backend_factory
