"""Shared envelope + allowlist for custom Deep Agents stream events.

These helpers are used by middleware (via ``langgraph.config.get_stream_writer``)
to publish tool lifecycle events, and by the loop / service layer to validate
and forward custom events through the IPC ``stream_event`` channel to the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_stream_writer

from app.progenitor.state.models import redact_secrets
from app.progenitor.streaming_utils import (
    DEFAULT_STREAM_ARGS_BYTES,
    DEFAULT_STREAM_PREVIEW_BYTES,
    truncate_json_safe,
    truncate_text,
)

logger = logging.getLogger(__name__)

# Reserved event_type values already used by the loop / service.
_RESERVED_EVENT_TYPES = frozenset(
    {"message_delta", "node_update", "reasoning_delta", "reasoning_summary"}
)

# Event types middleware/subagents may emit via get_stream_writer.
ALLOWED_CUSTOM_EVENT_TYPES = frozenset(
    {
        "tool_start",
        "tool_result",
        "tool_error",
        "subagent_start",
        "subagent_result",
        "debug_event",
    }
)

CUSTOM_EVENT_FALLBACK_TYPE = "custom_event"


def normalize_custom_event(payload: Any) -> dict[str, Any] | None:
    """Validate a custom stream payload and return a normalized envelope."""
    if not isinstance(payload, dict):
        event_type = CUSTOM_EVENT_FALLBACK_TYPE
        metadata: dict[str, Any] = {"raw": payload}
        content = ""
    else:
        raw_type = payload.get("event_type")
        event_type = raw_type.strip() if isinstance(raw_type, str) else ""
        metadata_in = payload.get("metadata")
        metadata = dict(metadata_in) if isinstance(metadata_in, dict) else {}
        content_in = payload.get("content")
        content = content_in if isinstance(content_in, str) else ""

        if event_type in _RESERVED_EVENT_TYPES or event_type not in ALLOWED_CUSTOM_EVENT_TYPES:
            if event_type:
                metadata.setdefault("original_event_type", event_type)
            event_type = CUSTOM_EVENT_FALLBACK_TYPE

    safe_metadata = truncate_json_safe(
        _redact_value(metadata),
        DEFAULT_STREAM_ARGS_BYTES,
    )
    if not isinstance(safe_metadata, dict):
        safe_metadata = {"value": safe_metadata}

    return {
        "event_type": event_type,
        "content": truncate_text(redact_secrets(content), DEFAULT_STREAM_PREVIEW_BYTES),
        "metadata": safe_metadata,
    }


def build_tool_start_event(
    tool_name: str,
    tool_call_id: str,
    raw_args: Any,
) -> dict[str, Any]:
    """Build a ``tool_start`` event payload with redacted+truncated args."""
    redacted = _redact_value(raw_args)
    return {
        "event_type": "tool_start",
        "content": tool_name,
        "metadata": {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "args_preview": truncate_json_safe(redacted, DEFAULT_STREAM_ARGS_BYTES),
        },
    }


def build_tool_result_event(
    tool_name: str,
    tool_call_id: str,
    duration_ms: float,
    result: Any,
) -> dict[str, Any]:
    """Build a ``tool_result`` event payload with redacted+truncated result."""
    return {
        "event_type": "tool_result",
        "content": tool_name,
        "metadata": {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "duration_ms": round(float(duration_ms), 2),
            "success": True,
            "result_preview": _redact_preview(result),
        },
    }


def build_tool_error_event(
    tool_name: str,
    tool_call_id: str,
    duration_ms: float,
    error: Any,
) -> dict[str, Any]:
    """Build a ``tool_error`` event payload with redacted+truncated error."""
    return {
        "event_type": "tool_error",
        "content": tool_name,
        "metadata": {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "duration_ms": round(float(duration_ms), 2),
            "success": False,
            "error": _redact_preview(error),
        },
    }


def build_reasoning_delta_event(
    content: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``reasoning_delta`` event payload for live summary fragments."""
    safe_content = truncate_text(redact_secrets(content), DEFAULT_STREAM_PREVIEW_BYTES)
    safe_metadata = _normalize_metadata(metadata)
    safe_metadata["text_length"] = len(safe_content)
    return {
        "event_type": "reasoning_delta",
        "content": safe_content,
        "metadata": safe_metadata,
    }


def build_reasoning_summary_event(
    content: str,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``reasoning_summary`` event payload for the final summary item."""
    safe_content = truncate_text(redact_secrets(content), DEFAULT_STREAM_ARGS_BYTES)
    safe_metadata = _normalize_metadata(metadata)
    safe_metadata["text_length"] = len(safe_content)
    return {
        "event_type": "reasoning_summary",
        "content": safe_content,
        "metadata": safe_metadata,
    }


def emit_stream_event(payload: dict[str, Any]) -> None:
    """Best-effort emission via LangGraph's stream writer."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    if writer is None:
        return
    try:
        writer(payload)
    except Exception as exc:
        logger.debug("Stream writer emission failed: %s", exc)


def _redact_value(value: Any) -> Any:
    """Return a JSON-friendly value with secrets redacted recursively."""
    if isinstance(value, Mapping):
        return {redact_secrets(str(key)): _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return [_redact_value(item) for item in value]
    if isinstance(value, bytes):
        return redact_secrets(value.decode("utf-8", errors="replace"))
    if isinstance(value, str):
        return redact_secrets(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    try:
        rendered = str(value)
    except Exception:
        rendered = repr(type(value))
    return redact_secrets(rendered)


def _redact_preview(value: Any) -> str:
    """Coerce ``value`` to a redacted, truncated string preview."""
    if value is None:
        return ""
    if hasattr(value, "content"):
        text = value.content
        if not isinstance(text, str):
            text = str(text)
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    redacted = redact_secrets(text) if text else ""
    return truncate_text(redacted, DEFAULT_STREAM_PREVIEW_BYTES)


def _normalize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    safe_metadata = truncate_json_safe(
        _redact_value(dict(metadata) if metadata is not None else {}),
        DEFAULT_STREAM_ARGS_BYTES,
    )
    if isinstance(safe_metadata, dict):
        return safe_metadata
    return {"value": safe_metadata}
