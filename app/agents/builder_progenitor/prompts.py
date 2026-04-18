"""Prompt loading helpers for builder_progenitor."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.progenitor.llm.prompts import (
    load_skill_context as load_repo_skill_context,
)
from app.progenitor.llm.prompts import (
    resolve_prompts_dir,
)


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the builder_progenitor prompt directory."""
    return resolve_prompts_dir() / "builder_progenitor"


def load_prompt(filename: str, **replacements: str) -> str:
    """Load a builder_progenitor prompt file and apply simple replacements."""
    prompt_path = get_prompts_dir() / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"builder_progenitor prompt file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return content


def load_skill_context(skill_names: list[str] | None = None) -> str:
    """Load repo-level prompt skills as plain text context."""
    selected = skill_names or [
        "build-agent",
        "git-workflow",
        "task-setup",
        "task-teardown",
    ]
    return load_repo_skill_context(
        selected,
        agent_name="builder_progenitor",
        prompts_dir=resolve_prompts_dir(),
    )
