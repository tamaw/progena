"""Shared helpers for DeepAgent/LangGraph streaming integration."""

from __future__ import annotations

import json
from typing import Any

# Hard caps to keep stream events well under the IPC 10MB envelope.
DEFAULT_STREAM_PREVIEW_BYTES = 1024  # tool result/error preview
DEFAULT_STREAM_ARGS_BYTES = 2048  # tool args preview
TRUNCATION_SUFFIX = "…[truncated]"


def make_json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def truncate_text(value: Any, max_bytes: int) -> str:
    """Coerce ``value`` to text and truncate to at most ``max_bytes`` UTF-8 bytes."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    suffix = TRUNCATION_SUFFIX.encode("utf-8")
    cut = max(0, max_bytes - len(suffix))
    return encoded[:cut].decode("utf-8", errors="ignore") + TRUNCATION_SUFFIX


def truncate_json_safe(value: Any, max_bytes: int) -> Any:
    """Return a JSON-safe copy of ``value`` whose serialized size fits ``max_bytes``.

    If the value serializes within the budget, it's returned as a structured
    JSON-safe value. Otherwise the JSON text is truncated and returned as a
    string preview to keep wire size bounded.
    """
    safe = make_json_safe(value)
    try:
        encoded = json.dumps(safe).encode("utf-8")
    except Exception:
        return truncate_text(value, max_bytes)
    if len(encoded) <= max_bytes:
        return safe
    return truncate_text(json.dumps(safe, ensure_ascii=False), max_bytes)


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
