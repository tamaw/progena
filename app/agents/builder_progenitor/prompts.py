"""Prompt loading helpers for builder_progenitor."""

from __future__ import annotations

import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path

from app.progenitor.llm.prompts import resolve_prompts_dir

logger = logging.getLogger(__name__)
DEFAULT_BUNDLED_PROMPTS_DIR = Path("/opt/progenitor/prompts")


@lru_cache(maxsize=1)
def get_prompts_dir() -> Path:
    """Return the builder_progenitor prompt directory."""
    return resolve_prompts_dir() / "builder_progenitor"


def get_bundled_prompts_root() -> Path | None:
    """Return the bundled fallback prompts root when available."""
    bundled_env = os.getenv("PROGENITOR_BUNDLED_PROMPTS_DIR", "").strip()
    bundled_root = (
        Path(bundled_env).expanduser().resolve()
        if bundled_env
        else DEFAULT_BUNDLED_PROMPTS_DIR
    )
    return bundled_root if bundled_root.is_dir() else None


def get_bundled_prompts_dir() -> Path | None:
    """Return the bundled fallback prompt directory for builder_progenitor."""
    bundled_root = get_bundled_prompts_root()
    if bundled_root is None:
        return None
    bundled_dir = bundled_root / "builder_progenitor"
    return bundled_dir if bundled_dir.is_dir() else None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def sync_missing_prompts() -> int:
    """Populate missing prompt files from the bundled image copy."""
    prompts_root = resolve_prompts_dir()
    bundled_root = get_bundled_prompts_root()
    if bundled_root is None or _same_path(prompts_root, bundled_root):
        return 0

    copied = 0
    for source_path in bundled_root.rglob("*.md"):
        relative_path = source_path.relative_to(bundled_root)
        destination_path = prompts_root / relative_path
        if destination_path.exists():
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        copied += 1

    if copied:
        logger.warning(
            "Seeded %s missing prompt file(s) into %s from %s",
            copied,
            prompts_root,
            bundled_root,
        )
    return copied


def _strip_frontmatter(content: str) -> str:
    """Remove optional markdown frontmatter from SKILL.md files."""
    if not content.startswith("---"):
        return content.strip()
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content.strip()
    return parts[2].strip()


def _find_prompt_path(filename: str) -> Path | None:
    sync_missing_prompts()

    prompt_path = get_prompts_dir() / filename
    if prompt_path.is_file():
        return prompt_path

    bundled_dir = get_bundled_prompts_dir()
    if bundled_dir is None:
        return None

    bundled_path = bundled_dir / filename
    if bundled_path.is_file():
        logger.warning(
            "builder_progenitor prompt file missing from %s; using bundled copy %s",
            get_prompts_dir(),
            bundled_path,
        )
        return bundled_path

    return None


def load_prompt(
    filename: str,
    *,
    fallback_filename: str | None = None,
    optional: bool = False,
    **replacements: str,
) -> str:
    """Load a builder_progenitor prompt file and apply simple replacements."""
    prompt_path = _find_prompt_path(filename)
    if prompt_path is None and fallback_filename is not None:
        prompt_path = _find_prompt_path(fallback_filename)
        if prompt_path is not None:
            logger.warning(
                "builder_progenitor prompt file %s missing; falling back to %s",
                filename,
                fallback_filename,
            )

    if prompt_path is None:
        if optional:
            return ""
        raise FileNotFoundError(
            f"builder_progenitor prompt file not found: {get_prompts_dir() / filename}"
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
