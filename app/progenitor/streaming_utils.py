"""Shared helpers for DeepAgent/LangGraph streaming integration."""

from __future__ import annotations

import json
from typing import Any


def make_json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def extract_stream_chunk_text(msg: Any) -> str:
    """Extract token text while preserving streaming whitespace."""
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).lower()
            if part_type in {"text", "output_text", "message"}:
                candidate = part.get("text") or part.get("content")
                if isinstance(candidate, str):
                    parts.append(candidate)
        return "".join(parts)

    return ""
