"""Path resolution helpers for prompt and skill content."""

from __future__ import annotations

import os
from pathlib import Path

# paths.py -> prompts/ -> progenitor/ -> app/ -> project_root/
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_PROMPTS_DIR = DEFAULT_DATA_DIR / "prompts"


def resolve_data_dir(data_dir: Path | str | None = None) -> Path:
    """Resolve the canonical data directory from explicit override, env, and default."""
    if data_dir is not None:
        return Path(data_dir).expanduser().resolve()

    env_data_dir = os.getenv("PROGENITOR_DATA_DIR", "").strip()
    if env_data_dir:
        resolved_env_dir = Path(env_data_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENITOR_DATA_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    if not DEFAULT_DATA_DIR.exists():
        raise FileNotFoundError(
            f"Data directory not found: {DEFAULT_DATA_DIR}. "
            "Ensure the data/ directory exists at the project root."
        )
    return DEFAULT_DATA_DIR


def resolve_prompts_dir(prompts_dir: Path | str | None = None) -> Path:
    """Resolve prompt directory from explicit override, env, and project default."""
    if prompts_dir is not None:
        return Path(prompts_dir).expanduser().resolve()

    env_prompts_dir = os.getenv("PROGENITOR_PROMPTS_DIR", "").strip()
    if env_prompts_dir:
        resolved_env_dir = Path(env_prompts_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENITOR_PROMPTS_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    resolved_default_dir = resolve_data_dir() / "prompts"
    if not resolved_default_dir.exists():
        raise FileNotFoundError(
            f"Prompts directory not found: {resolved_default_dir}. "
            "Ensure the data/prompts/ directory exists at the project root."
        )
    return resolved_default_dir


def resolve_project_root(prompts_dir: Path | str | None = None) -> Path:
    """Infer the project root from the resolved prompts directory."""
    resolved_prompts_dir = resolve_prompts_dir(prompts_dir)
    for candidate in resolved_prompts_dir.parents:
        if (candidate / "app").is_dir():
            return candidate
    return resolved_prompts_dir.parent
