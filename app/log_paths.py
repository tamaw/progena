from __future__ import annotations

import os
from pathlib import Path


def resolve_agent_log_dir(
    base_log_dir: str | Path,
    *,
    fallback_agent_name: str | None = None,
) -> Path:
    """Return the per-agent log directory under the shared log root.

    Runtime containers set ``AGENT_NAME`` via Dockerfiles and GitHub Actions.
    The fallback keeps local tests and direct module usage working when that
    environment variable is not populated.
    """
    agent_name = os.getenv("AGENT_NAME", "").strip() or fallback_agent_name
    if not agent_name:
        raise RuntimeError("AGENT_NAME must be set to resolve the agent log directory")
    return Path(base_log_dir) / agent_name
