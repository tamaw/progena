"""YAML front matter parsing shared by knowledge pages and instructions."""

from __future__ import annotations

import re
from typing import Any

import yaml


def split_front_matter(content: str) -> tuple[dict[str, Any] | None, str]:
    """Split ``---``-delimited YAML front matter from the body.

    Returns ``(None, content)`` when the text has no front matter. Raises
    ``ValueError`` for an unclosed block, invalid YAML, or a non-mapping.
    """
    if not content.startswith("---\n"):
        return None, content
    match = re.search(r"^---\s*$", content[4:], flags=re.MULTILINE)
    if match is None:
        raise ValueError("unclosed YAML front matter")
    end = 4 + match.start()
    body_start = 4 + match.end()
    if content[body_start : body_start + 1] == "\n":
        body_start += 1
    try:
        parsed = yaml.safe_load(content[4:end])
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("YAML front matter must be a mapping")
    return parsed, content[body_start:]
