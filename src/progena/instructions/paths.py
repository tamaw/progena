"""Path resolution for the data directory and its checked-in instructions."""

from __future__ import annotations

import os
from pathlib import Path

# paths.py -> instructions/ -> progena/ -> src/ -> project_root/
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_INSTRUCTIONS_DIR = DEFAULT_DATA_DIR / "instructions"


def resolve_data_dir(data_dir: Path | str | None = None) -> Path:
    """Resolve the canonical data directory from explicit override, env, and default."""
    if data_dir is not None:
        return Path(data_dir).expanduser().resolve()

    env_data_dir = os.getenv("PROGENA_DATA_DIR", "").strip()
    if env_data_dir:
        resolved_env_dir = Path(env_data_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENA_DATA_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    if not DEFAULT_DATA_DIR.exists():
        raise FileNotFoundError(
            f"Data directory not found: {DEFAULT_DATA_DIR}. "
            "Ensure the data/ directory exists at the project root."
        )
    return DEFAULT_DATA_DIR


def resolve_instructions_dir(instructions_dir: Path | str | None = None) -> Path:
    """Resolve the instructions directory from explicit override, env, and project default."""
    if instructions_dir is not None:
        return Path(instructions_dir).expanduser().resolve()

    env_dir = os.getenv("PROGENA_INSTRUCTIONS_DIR", "").strip()
    if env_dir:
        resolved_env_dir = Path(env_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENA_INSTRUCTIONS_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    resolved_default_dir = resolve_data_dir() / "instructions"
    if not resolved_default_dir.exists():
        raise FileNotFoundError(
            f"Instructions directory not found: {resolved_default_dir}. "
            "Ensure the data/instructions/ directory exists at the project root."
        )
    return resolved_default_dir
