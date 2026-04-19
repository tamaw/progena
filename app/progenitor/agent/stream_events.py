"""Shared envelope + allowlist for custom Deep Agents stream events.

These helpers are used by middleware (via ``langgraph.config.get_stream_writer``)
to publish tool lifecycle events, and by the loop / service layer to validate
and forward custom events through the IPC ``stream_event`` channel to the UI.
"""

from __future__ import annotations

import logging
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
_RESERVED_EVENT_TYPES = frozenset({"message_delta", "node_update"})

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
    """Validate a custom stream payload and return a normalized envelope.

    Accepts dicts shaped like ``{"event_type": str, "content": str,
    "metadata": dict}``. Unknown or invalid event_types are coerced to
    ``custom_event`` and the original payload is preserved under
    ``metadata.raw``. Returns ``None`` if the payload cannot be turned into
    a usable envelope at all.
    """
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
        _redact_args(metadata),
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
    redacted = _redact_args(raw_args)
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


def emit_stream_event(payload: dict[str, Any]) -> None:
    """Best-effort emission via LangGraph's stream writer.

    Silently no-ops if we are not inside a LangGraph streaming context.
    Tool execution must never depend on emission.
    """
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # Not inside a graph context (e.g. plain unit test) — silently skip.
        return
    if writer is None:
        return
    try:
        writer(payload)
    except Exception as exc:
        logger.debug("Stream writer emission failed: %s", exc)


def _redact_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_args(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_args(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


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
