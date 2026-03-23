"""Prompt loading helpers for builder_progenitor."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.progenitor.llm.prompts import resolve_prompts_dir


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the builder_progenitor prompt directory."""
    return resolve_prompts_dir() / "builder_progenitor"


def _strip_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()


def load_prompt(filename: str, **replacements: str) -> str:
    """Load a builder_progenitor prompt file and apply simple replacements."""
    prompt_path = get_prompts_dir() / filename
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"builder_progenitor prompt file not found: {prompt_path}"
        )

    content = prompt_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"{{{key}}}", str(value))
    return content


def load_skill_context(skill_names: list[str] | None = None) -> str:
    """Load repo-level prompt skills as plain text context."""
    prompts_dir = get_prompts_dir().parent
    skills_dir = prompts_dir / "skills"
    if not skills_dir.is_dir():
        return ""

    selected = skill_names or ["build-agent", "git-workflow"]
    sections: list[str] = []
    for skill_name in selected:
        skill_file = skills_dir / skill_name / "SKILL.md"
        if not skill_file.is_file():
            continue
        body = _strip_frontmatter(skill_file.read_text(encoding="utf-8"))
        if body:
            sections.append(body)
    return "\n\n".join(sections)
