"""Prompt loading helpers for builder.

Self-contained: does not import from any other ``app/agents/*`` agent or from
``app.progenitor.*``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ._paths import resolve_prompts_dir, resolve_skills_dir, strip_skill_frontmatter


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the builder prompt directory."""
    return resolve_prompts_dir() / "builder"


def load_prompt(filename: str, **replacements: str) -> str:
    """Load a builder prompt file and apply simple ``{key}`` replacements."""
    prompt_path = get_prompts_dir() / filename
    if not prompt_path.exists():
        raise FileNotFoundError(f"builder prompt file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return content


_DEFAULT_SKILLS = ("build-agent", "git-workflow", "task-setup", "task-teardown")


def load_skill_context(skill_names: list[str] | None = None) -> str:
    """Load builder skill bundles from ``data/skills/builder/`` as plain text."""
    selected = list(skill_names) if skill_names else list(_DEFAULT_SKILLS)
    skills_dir = resolve_skills_dir("builder")
    if not skills_dir.is_dir():
        raise FileNotFoundError(
            f"Builder skills directory not found: {skills_dir}. "
            "Ensure data/skills/builder/ exists and is mounted."
        )

    sections: list[str] = []
    for skill_name in selected:
        skill_file = skills_dir / skill_name / "SKILL.md"
        if not skill_file.is_file():
            raise FileNotFoundError(f"Skill file not found: {skill_file}")
        body = strip_skill_frontmatter(skill_file.read_text(encoding="utf-8"))
        if body:
            sections.append(body)
    return "\n\n".join(sections)
