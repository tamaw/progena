"""Message coercion, serialization, and response extraction helpers."""

from __future__ import annotations

import json
from typing import Any

from ..ipc.stream_payloads import extract_stream_chunk_text


def coerce_messages_list(messages: Any) -> list:
    """Coerce LangGraph/DeepAgent message updates into a plain list."""
    if messages is None:
        return []
    if isinstance(messages, list):
        return messages
    value = getattr(messages, "value", None)
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(messages, dict):
        return [messages]
    try:
        return list(messages)
    except TypeError:
        return []


def serialize_messages(messages: Any) -> list:
    """Serialize message objects for checkpoint."""
    serialized = []
    for msg in coerce_messages_list(messages):
        if hasattr(msg, "model_dump"):
            serialized.append(msg.model_dump())
        elif isinstance(msg, dict):
            serialized.append(msg)
    return serialized


def get_last_ai_message(messages: Any) -> Any | None:
    """Get the last assistant/AI message from a messages collection."""
    for msg in reversed(coerce_messages_list(messages)):
        msg_type = str(getattr(msg, "type", "")).lower()
        msg_role = str(getattr(msg, "role", "")).lower()
        if msg_type == "ai" or msg_role == "assistant":
            return msg
        if isinstance(msg, dict):
            role = str(msg.get("role", "")).lower()
            mtype = str(msg.get("type", "")).lower()
            if role == "assistant" or mtype == "ai":
                return msg
    return None


def _coerce_to_dict(obj: Any) -> dict[str, Any] | None:
    """Convert a dict or Pydantic model to a plain dict, returning None for other types."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            dumped = obj.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else None
        except Exception:  # nosec B110
            pass
    return None


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    try:
        text = json.dumps(value, default=str).strip()
    except Exception:
        text = str(value).strip()
    return text or None


def extract_reasoning_trace(msg: Any) -> str | None:
    """Extract provider reasoning/thinking metadata from a message."""
    if msg is None:
        return None

    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            part_dict = _coerce_to_dict(part)
            if part_dict is None:
                continue
            part_type = str(part_dict.get("type", "")).lower()
            if part_type not in {
                "reasoning",
                "reasoning_text",
                "reasoning_summary",
                "thinking",
            }:
                continue
            if text := extract_reasoning_summary_text({"content": [part_dict]}):
                parts.append(text)
                continue
            candidate = (
                part_dict.get("text") or part_dict.get("summary") or part_dict.get("content")
            )
            if text := _coerce_text(candidate):
                parts.append(text)
        if parts:
            return "\n".join(parts)

    containers: list[dict[str, Any]] = []
    if isinstance(msg, dict):
        containers.append(msg)
    else:
        for attr in ("additional_kwargs", "response_metadata"):
            value = getattr(msg, attr, None)
            if isinstance(value, dict):
                containers.append(value)

    for container in containers:
        for key in ("reasoning", "thinking"):
            if key in container and (text := _coerce_text(container.get(key))):
                return text
        if "summary" in container or "effort" in container:
            summary_obj = {
                "effort": container.get("effort"),
                "summary": container.get("summary"),
            }
            if text := _coerce_text(summary_obj):
                return text

    return None


def extract_message_text(msg: Any) -> str | None:
    """Extract assistant-facing text from message content payloads."""
    if msg is None:
        return None

    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    if isinstance(content, str):
        text = content.strip()
        return text or None

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                text = part.strip()
                if text:
                    parts.append(text)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", "")).lower()
            if part_type in {"text", "output_text", "message"}:
                candidate = part.get("text") or part.get("content")
                if isinstance(candidate, str) and candidate.strip():
                    parts.append(candidate.strip())
        if parts:
            return "\n".join(parts)

    return None


def summarize_message_schema(msg: Any) -> dict[str, Any]:
    """Return a compact schema summary for assistant messages."""
    if isinstance(msg, dict):
        additional = msg.get("additional_kwargs")
        metadata = msg.get("response_metadata")
        content = msg.get("content")
        return {
            "type": msg.get("type"),
            "role": msg.get("role"),
            "keys": sorted(msg.keys()),
            "additional_kwargs_keys": sorted(additional.keys())
            if isinstance(additional, dict)
            else [],
            "response_metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
            "content_type": type(content).__name__,
        }

    additional = getattr(msg, "additional_kwargs", None)
    metadata = getattr(msg, "response_metadata", None)
    content = getattr(msg, "content", None)
    return {
        "type": getattr(msg, "type", None),
        "role": getattr(msg, "role", None),
        "additional_kwargs_keys": sorted(additional.keys()) if isinstance(additional, dict) else [],
        "response_metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
        "content_type": type(content).__name__,
    }


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


__all__ = [
    "coerce_messages_list",
    "extract_message_text",
    "extract_reasoning_trace",
    "extract_reasoning_token_count",
    "extract_stream_chunk_text",
    "get_last_ai_message",
    "serialize_messages",
    "summarize_message_schema",
]
