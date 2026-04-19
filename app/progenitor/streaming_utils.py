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


def extract_stream_reasoning_deltas(msg: Any) -> list[str]:
    """Extract streamed reasoning-summary text fragments from a message chunk."""
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if not isinstance(content, list):
        return []

    deltas: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type", "")).lower()
        if part_type not in {"reasoning", "reasoning_text", "reasoning_summary", "thinking"}:
            continue

        summary = part.get("summary")
        if isinstance(summary, list):
            for summary_part in summary:
                if not isinstance(summary_part, dict):
                    continue
                summary_type = str(summary_part.get("type", "")).lower()
                if summary_type not in {"summary_text", "text", ""}:
                    continue
                summary_text = summary_part.get("text") or summary_part.get("content")
                if isinstance(summary_text, str) and summary_text:
                    deltas.append(summary_text)
            continue

        candidate = part.get("text") or part.get("content")
        if isinstance(candidate, str) and candidate:
            deltas.append(candidate)

    return deltas


def extract_reasoning_summary_text(msg: Any) -> str | None:
    """Extract the provider reasoning summary text from a completed message."""
    if msg is None:
        return None

    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).lower()
            if part_type not in {"reasoning", "reasoning_text", "reasoning_summary", "thinking"}:
                continue
            if text := _coerce_reasoning_summary_text(part):
                parts.append(text)
        if parts:
            return "\n\n".join(parts)

    containers: list[dict[str, Any]] = []
    if isinstance(msg, dict):
        containers.append(msg)
        for key in ("additional_kwargs", "response_metadata"):
            value = msg.get(key)
            if isinstance(value, dict):
                containers.append(value)
    else:
        for attr in ("additional_kwargs", "response_metadata"):
            value = getattr(msg, attr, None)
            if isinstance(value, dict):
                containers.append(value)

    for container in containers:
        for key in ("reasoning", "thinking", "summary"):
            if key in container and (text := _coerce_reasoning_summary_text(container.get(key))):
                return text

    return None


def extract_reasoning_token_count(msg: Any) -> int | None:
    """Extract provider-reported reasoning token count from a completed message."""
    if msg is None:
        return None

    containers: list[dict[str, Any]] = []
    if isinstance(msg, dict):
        for key in ("usage_metadata", "response_metadata"):
            value = msg.get(key)
            if isinstance(value, dict):
                containers.append(value)
    else:
        for attr in ("usage_metadata", "response_metadata"):
            value = getattr(msg, attr, None)
            if isinstance(value, dict):
                containers.append(value)

    for container in containers:
        token_count = _extract_reasoning_token_count_from_container(container)
        if token_count is not None:
            return token_count

    return None


def _coerce_reasoning_summary_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if text := _coerce_reasoning_summary_text(item):
                parts.append(text)
        if parts:
            return "\n\n".join(parts)
        return None
    if isinstance(value, dict):
        summary = value.get("summary")
        if summary is not None and (text := _coerce_reasoning_summary_text(summary)):
            return text
        candidate = value.get("text") or value.get("content")
        if isinstance(candidate, str):
            stripped = candidate.strip()
            return stripped or None
        return None
    try:
        rendered = str(value).strip()
    except Exception:
        return None
    return rendered or None


def _extract_reasoning_token_count_from_container(container: dict[str, Any]) -> int | None:
    detail_candidates = [
        container.get("output_token_details"),
        container.get("output_tokens_details"),
        container.get("completion_tokens_details"),
        container.get("token_usage"),
        container.get("usage"),
    ]
    for details in detail_candidates:
        if not isinstance(details, dict):
            continue
        token_count = _extract_reasoning_token_count_from_container(details)
        if token_count is not None:
            return token_count

    for key in ("reasoning", "reasoning_tokens"):
        value = container.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)

    return None
