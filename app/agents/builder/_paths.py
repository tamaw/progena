"""Path resolution helpers for the builder agent.

Inlined so the builder package does not import from ``app.progenitor`` and
remains an isolated agent project.
"""

from __future__ import annotations

import os
from pathlib import Path

# app/agents/builder/_paths.py -> repo root is parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    """Return the project repo root (parent of ``app/`` and ``data/``)."""
    return _REPO_ROOT


def resolve_data_dir() -> Path:
    """Resolve the data directory.

    Resolution order:
    1. ``PROGENITOR_DATA_DIR`` environment variable (must exist)
    2. ``<repo_root>/data`` (must exist)
    """
    env_data_dir = os.getenv("PROGENITOR_DATA_DIR", "").strip()
    if env_data_dir:
        resolved = Path(env_data_dir).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"PROGENITOR_DATA_DIR points to missing directory: {resolved}")
        return resolved
    default_dir = _REPO_ROOT / "data"
    if not default_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {default_dir}")
    return default_dir


def resolve_prompts_dir() -> Path:
    """Resolve the prompts root directory.

    Resolution order:
    1. ``PROGENITOR_PROMPTS_DIR`` environment variable (must exist)
    2. ``<data_dir>/prompts`` (must exist)
    """
    env_prompts_dir = os.getenv("PROGENITOR_PROMPTS_DIR", "").strip()
    if env_prompts_dir:
        resolved = Path(env_prompts_dir).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(
                f"PROGENITOR_PROMPTS_DIR points to missing directory: {resolved}"
            )
        return resolved
    default_dir = resolve_data_dir() / "prompts"
    if not default_dir.exists():
        raise FileNotFoundError(f"Prompts directory not found: {default_dir}")
    return default_dir


def resolve_skills_root() -> Path:
    """Return the checked-in skills root adjacent to the resolved prompts dir."""
    return resolve_prompts_dir().parent / "skills"


def resolve_skills_dir(agent_name: str) -> Path:
    """Return the agent-scoped skills directory under ``data/skills/``."""
    return resolve_skills_root() / agent_name


def strip_skill_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()
