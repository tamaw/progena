"""Local prompt-file loading helpers for builder_progenitor.

Self-contained copy of the prompt loading semantics: builder_progenitor
does not depend on app.progenitor.

Skill *content* loading (``load_skill_context``) lives in
``postgres_skills.py`` instead -- skills are Postgres-backed marketplace-plugin
content now, not files under a prompts-relative directory. This module
re-exports it so existing callers (``from .prompt_support import
load_skill_context``) don't need to change their import path.
"""

from __future__ import annotations

import os
from pathlib import Path

from .postgres_skills import load_skill_context, strip_skill_frontmatter

__all__ = [
    "DEFAULT_INSTRUCTIONS_DIR",
    "load_skill_context",
    "load_prompt",
    "render_prompt_template",
    "resolve_instructions_dir",
    "strip_front_matter",
    "strip_skill_frontmatter",
]

# prompt_support.py -> builder_progenitor/ -> agents/ -> app/ -> project_root/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INSTRUCTIONS_DIR = _PROJECT_ROOT / "data" / "instructions"


def resolve_instructions_dir(instructions_dir: Path | str | None = None) -> Path:
    """Resolve prompt directory from explicit override, env, and project default."""
    if instructions_dir is not None:
        return Path(instructions_dir).expanduser().resolve()

    env_instructions_dir = os.getenv("PROGENITOR_INSTRUCTIONS_DIR", "").strip()
    if env_instructions_dir:
        resolved_env_dir = Path(env_instructions_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENITOR_INSTRUCTIONS_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    if not DEFAULT_INSTRUCTIONS_DIR.exists():
        raise FileNotFoundError(
            f"Instructions directory not found: {DEFAULT_INSTRUCTIONS_DIR}. "
            "Ensure the data/instructions/ directory exists at the project root."
        )
    return DEFAULT_INSTRUCTIONS_DIR


def render_prompt_template(content: str, **replacements: object) -> str:
    """Apply simple {placeholder} replacements to prompt content."""
    rendered = content
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def strip_front_matter(content: str) -> str:
    """Return an instruction's body without its leading YAML front matter block."""
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 3)
    if end == -1:
        raise ValueError("unclosed YAML front matter")
    return content[end + 5 :]


def load_prompt(
    filename: str,
    *,
    instructions_dir: Path | str | None = None,
    **replacements: object,
) -> str:
    """Load a prompt file and apply simple template replacements."""
    prompt_root = resolve_instructions_dir(instructions_dir)
    prompt_file = prompt_root / filename
    if not prompt_file.exists():
        searched_dirs = [str(prompt_root)]
        if prompt_root != DEFAULT_INSTRUCTIONS_DIR:
            searched_dirs.append(str(DEFAULT_INSTRUCTIONS_DIR))
        raise FileNotFoundError(
            f"Prompt file not found: {prompt_file}. "
            f"Ensure data/instructions/{filename} exists. Searched prompt directories: {', '.join(searched_dirs)}"
        )

    content = strip_front_matter(prompt_file.read_text(encoding="utf-8"))
    return render_prompt_template(content, **replacements)
