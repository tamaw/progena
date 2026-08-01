"""Local prompt-file loading helpers for builder_agent.

Self-contained copy of the prompt loading semantics: builder_agent does
not depend on app.progenitor. Behavior matches what app/progenitor/prompts/
used to provide, but reuses this package's own path configuration
(``config.DEFAULT_PROJECT_PROMPTS_DIR``) instead of a shared module.

Skill *content* loading (``load_skill_context``) lives in
``postgres_skills.py`` instead -- skills are Postgres-backed marketplace-plugin
content now, not files under a prompts-relative directory. This module
re-exports it so existing callers (``from .prompt_support import
load_skill_context``) don't need to change their import path.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .postgres_skills import load_skill_context, strip_skill_frontmatter

__all__ = [
    "DEFAULT_PROMPTS_DIR",
    "load_skill_context",
    "load_prompt",
    "render_prompt_template",
    "resolve_prompts_dir",
    "strip_skill_frontmatter",
]

DEFAULT_PROMPTS_DIR = config.DEFAULT_PROJECT_PROMPTS_DIR


def resolve_prompts_dir(prompts_dir: Path | str | None = None) -> Path:
    """Resolve the prompt directory from explicit override or the agent default."""
    if prompts_dir is not None:
        return Path(prompts_dir).expanduser().resolve()
    if not DEFAULT_PROMPTS_DIR.exists():
        raise FileNotFoundError(
            f"Prompts directory not found: {DEFAULT_PROMPTS_DIR}. "
            "Ensure the data/prompts/ directory exists at the project root."
        )
    return DEFAULT_PROMPTS_DIR


def render_prompt_template(content: str, **replacements: object) -> str:
    """Apply simple {placeholder} replacements to prompt content."""
    rendered = content
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def load_prompt(
    filename: str,
    *,
    prompts_dir: Path | str | None = None,
    **replacements: object,
) -> str:
    """Load a prompt file and apply simple template replacements."""
    prompt_root = resolve_prompts_dir(prompts_dir)
    prompt_file = prompt_root / filename
    if not prompt_file.exists():
        searched_dirs = [str(prompt_root)]
        if prompt_root != DEFAULT_PROMPTS_DIR:
            searched_dirs.append(str(DEFAULT_PROMPTS_DIR))
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}. "
            f"Ensure data/prompts/{filename} exists. Searched prompt directories: {', '.join(searched_dirs)}"
        )

    content = prompt_file.read_text(encoding="utf-8")
    return render_prompt_template(content, **replacements)


