"""Prompt and skill loading helpers for Progenitor."""

from .loader import load_prompt, render_prompt_template
from .paths import (
    DEFAULT_DATA_DIR,
    DEFAULT_PROMPTS_DIR,
    PROJECT_ROOT,
    resolve_data_dir,
    resolve_project_root,
    resolve_prompts_dir,
    resolve_skills_dir,
    resolve_skills_root,
)
from .skills import load_skill_context, strip_skill_frontmatter

__all__ = [
    "DEFAULT_DATA_DIR",
    "DEFAULT_PROMPTS_DIR",
    "PROJECT_ROOT",
    "load_prompt",
    "load_skill_context",
    "render_prompt_template",
    "resolve_data_dir",
    "resolve_project_root",
    "resolve_prompts_dir",
    "resolve_skills_dir",
    "resolve_skills_root",
    "strip_skill_frontmatter",
]
