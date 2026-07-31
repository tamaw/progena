"""Prompt loading helpers for builder_progenitor."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .prompt_support import load_prompt as load_repo_prompt
from .prompt_support import load_skill_context as load_repo_skill_context
from .prompt_support import resolve_prompts_dir


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the builder_progenitor prompt directory."""
    return resolve_prompts_dir() / "builder_progenitor"


def load_prompt(filename: str, **replacements: str) -> str:
    """Load a builder_progenitor prompt file and apply simple replacements."""
    return load_repo_prompt(
        f"builder_progenitor/{filename}",
        prompts_dir=resolve_prompts_dir(),
        **replacements,
    )


async def load_skill_context(skill_names: list[str] | None = None) -> str:
    """Load builder skills as plain text context from Progenitor's shared
    Postgres store (the installed ``builder-progenitor`` marketplace plugin)."""
    selected = skill_names or [
        "build-agent",
        "git-workflow",
        "task-setup",
        "task-teardown",
    ]
    return await load_repo_skill_context(selected, agent_name="builder_progenitor")
