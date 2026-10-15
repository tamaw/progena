"""Prompt loading helpers for observe_agent."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .prompt_support import load_prompt as load_repo_prompt
from .prompt_support import resolve_instructions_dir


@lru_cache(maxsize=1)
def get_instructions_dir(instructions_dir: Path | str | None = None) -> Path:
    """Return the observe_agent prompt directory."""
    return resolve_instructions_dir(instructions_dir) / "observe_agent"


def load_prompt(
    filename: str,
    *,
    instructions_dir: Path | str | None = None,
    **replacements: str,
) -> str:
    """Load an observe_agent prompt file and apply simple replacements."""
    return load_repo_prompt(
        f"observe_agent/{filename}",
        instructions_dir=resolve_instructions_dir(instructions_dir),
        **replacements,
    )
