"""Local prompt-file and skill-bundle loading helpers for builder_agent.

Self-contained copy of the prompt/skill loading semantics: builder_agent does
not depend on app.progenitor. Behavior matches what app/progenitor/prompts/
used to provide, but reuses this package's own path configuration
(``config.DEFAULT_PROJECT_PROMPTS_DIR``) instead of a shared module.
"""

from __future__ import annotations

from pathlib import Path

from . import config

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


def resolve_skills_dir(agent_name: str, prompts_dir: Path | str | None = None) -> Path:
    """Return the agent-scoped skills directory under data/skills/."""
    return resolve_prompts_dir(prompts_dir).parent / "skills" / agent_name


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
