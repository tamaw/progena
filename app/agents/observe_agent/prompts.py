"""Prompt loading helpers for observe_agent."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.progenitor.llm.prompts import resolve_prompts_dir


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the observe_agent prompt directory."""
    return resolve_prompts_dir() / "observe_agent"


def load_prompt(filename: str, **replacements: str) -> str:
    """Load an observe_agent prompt file and apply simple replacements."""
    prompt_path = get_prompts_dir() / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"observe_agent prompt file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return content
