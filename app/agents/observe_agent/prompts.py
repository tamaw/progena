"""Prompt loading helpers for observe_agent."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .prompt_support import load_prompt as load_repo_prompt
from .prompt_support import resolve_prompts_dir


@lru_cache(maxsize=1)
def get_prompts_dir(prompts_dir: Path | str | None = None) -> Path:
    """Return the observe_agent prompt directory."""
    return resolve_prompts_dir(prompts_dir) / "observe_agent"


def load_prompt(
    filename: str,
    *,
    prompts_dir: Path | str | None = None,
    **replacements: str,
) -> str:
    """Load an observe_agent prompt file and apply simple replacements."""
    return load_repo_prompt(
        f"observe_agent/{filename}",
        prompts_dir=resolve_prompts_dir(prompts_dir),
        **replacements,
    )
