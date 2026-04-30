"""Helpers for extracting graph results from LangGraph message payloads."""

from typing import Any


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
