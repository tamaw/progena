"""Prompt file loading and simple template rendering helpers."""

from __future__ import annotations

from pathlib import Path

from .paths import DEFAULT_PROMPTS_DIR, resolve_prompts_dir


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
