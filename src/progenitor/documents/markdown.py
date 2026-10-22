"""Markdown capabilities."""

from __future__ import annotations

from typing import Any

import yaml


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from a markdown body."""
    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter opened with '---' but never closed")
    try:
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")
    return data, "\n".join(lines[end + 1 :])
