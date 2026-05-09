"""Skill bundle loading helpers for prompt assembly."""

from __future__ import annotations

from pathlib import Path

from .paths import resolve_skills_dir


def strip_skill_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()


def load_skill_context(
    skill_names: list[str],
    *,
    agent_name: str,
    prompts_dir: Path | str | None = None,
    fallback_agents: tuple[str, ...] = (),
) -> str:
    """Load skill bundles from agent-scoped skills directories."""
    search_agents: list[str] = []
    for candidate in (agent_name, *fallback_agents):
        if candidate not in search_agents:
            search_agents.append(candidate)
    search_dirs = [resolve_skills_dir(candidate, prompts_dir) for candidate in search_agents]
    if not any(path.is_dir() for path in search_dirs):
        searched = ", ".join(str(path) for path in search_dirs)
        raise FileNotFoundError(
            f"Skills directory not found. Searched: {searched}. "
            "Ensure data/skills/<agent-name>/ is mounted and populated."
        )

    sections: list[str] = []
    for skill_name in skill_names:
        searched_files = [skills_dir / skill_name / "SKILL.md" for skills_dir in search_dirs]
        skill_file = next((path for path in searched_files if path.is_file()), None)
        if skill_file is None:
            searched = ", ".join(str(path) for path in searched_files)
            raise FileNotFoundError(
                f"Skill file not found. Searched: {searched}. "
                "Ensure data/skills/<agent-name>/<skill-name>/SKILL.md exists."
            )
        body = strip_skill_frontmatter(skill_file.read_text(encoding="utf-8"))
        if body:
            sections.append(body)
    return "\n\n".join(sections)
