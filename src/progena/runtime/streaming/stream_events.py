"""Shared envelope + allowlist for custom Deep Agents stream events.

These helpers are used by middleware (via ``langgraph.config.get_stream_writer``)
to publish tool lifecycle events, and by the SSE stream bridge to validate and
forward custom events through the SSE ``stream_event`` channel to the UI.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import Command

from src.progena.runtime.streaming.stream_payloads import (
    DEFAULT_STREAM_ARGS_BYTES,
    DEFAULT_STREAM_PREVIEW_BYTES,
    TRUNCATION_SUFFIX,
    truncate_json_safe,
    truncate_text,
)
from src.progena.security.redaction import redact_secrets
from src.progena.sessions.messages import tool_result_failed

logger = logging.getLogger(__name__)

# Reserved event_type values already used by the loop / service.
_RESERVED_EVENT_TYPES = frozenset(
    {
        "message_delta",
        "node_update",
        "reasoning_delta",
        "reasoning_summary",
        "todos_update",
        "subagent_message_delta",
    }
)

# Event types middleware/subagents may emit via get_stream_writer.
ALLOWED_CUSTOM_EVENT_TYPES = frozenset(
    {
        "tool_start",
        "tool_result",
        "tool_error",
        "subagent_start",
        "subagent_result",
        "subagent_tool_start",
        "subagent_tool_result",
        "subagent_tool_error",
        "async_subagent_start",
        "async_subagent_update",
        "async_subagent_pause",
        "async_subagent_result",
        "async_subagent_cancel",
        "a2a_call_progress",
        "conversation_summary_started",
        "conversation_summary_finished",
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

    safe_metadata = _normalize_metadata(metadata)

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
    metadata: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "args_preview": truncate_json_safe(redacted, DEFAULT_STREAM_ARGS_BYTES),
    }
    if tool_name == "task" and isinstance(redacted, dict):
        # Keep correlation and a compact task summary outside the args budget.
        # Large descriptions can turn args_preview into a truncated string,
        # which would otherwise hide the fields needed to rebuild the card.
        subagent_type = redacted.get("subagent_type")
        if isinstance(subagent_type, str):
            metadata["subagent_type"] = subagent_type
        description = redacted.get("description")
        if isinstance(description, str):
            metadata["task_summary"] = truncate_text(description, 140)
    return {
        "event_type": "tool_start",
        "content": tool_name,
        "metadata": metadata,
    }


def build_tool_result_event(
    tool_name: str,
    tool_call_id: str,
    duration_ms: float,
    result: Any,
) -> dict[str, Any]:
    """Build a ``tool_result`` event payload with redacted+truncated result.

    Image content items are extracted to a top-level ``images`` key so they
    bypass the 2 KB metadata truncation applied in ``normalize_custom_event``.
    """
    result = _unwrap_command_result(result)
    images = _extract_images(result)
    text = _result_text(result)
    payload: dict[str, Any] = {
        "event_type": "tool_result",
        "content": tool_name,
        "metadata": {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "duration_ms": round(float(duration_ms), 2),
            # Classified before truncation; the preview may lose the failure footer.
            "success": not tool_result_failed(tool_name, result, text),
            "result_preview": (
                f"{len(images)} image{'s' if len(images) != 1 else ''}"
                if images
                else _redact_preview(text)
            ),
        },
    }
    artifact = _extract_artifact(result)
    if artifact is not None:
        payload["metadata"]["artifact"] = artifact
    if images:
        payload["images"] = images
    return payload


def _extract_images(result: Any) -> list[dict[str, str]]:
    """Return image content items from a tool result.

    Accepts a raw list, a ToolMessage, or any object with a ``content``
    attribute whose value is a list of LangChain content blocks.
    """
    if hasattr(result, "content"):
        result = result.content
    if not isinstance(result, list):
        return []
    images = []
    for item in result:
        if not isinstance(item, dict):
            if hasattr(item, "model_dump"):
                try:
                    item = item.model_dump(mode="json")
                except Exception:  # nosec B112
                    continue
            elif hasattr(item, "__dict__"):
                item = vars(item)
            else:
                continue
        if str(item.get("type", "")).lower() != "image":
            continue
        base64_data = item.get("base64") or item.get("data") or item.get("url") or ""
        if not isinstance(base64_data, str) or not base64_data:
            continue
        images.append(
            {
                "id": str(item.get("id") or ""),
                "base64": base64_data,
                "media_type": str(item.get("media_type") or item.get("mime_type") or "image/jpeg"),
            }
        )
    return images


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


def build_a2a_call_progress_event(
    tool_call_id: str,
    agent_name: str,
    state: str,
    message_text: str,
) -> dict[str, Any]:
    """Build an ``a2a_call_progress`` event for an intermediate A2A status update."""
    safe_text = truncate_text(redact_secrets(message_text), DEFAULT_STREAM_PREVIEW_BYTES)
    return {
        "event_type": "a2a_call_progress",
        "content": safe_text,
        "metadata": {
            "tool_call_id": tool_call_id,
            "agent_name": agent_name,
            "state": state,
            "message_text": safe_text,
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
    safe_metadata["summary"] = safe_content
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
    safe_metadata["summary"] = safe_content
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


_ARTIFACT_FIELDS = ("summary", "count", "kind", "status")
_ARTIFACT_STRING_BYTES = DEFAULT_STREAM_PREVIEW_BYTES
_ARTIFACT_TOTAL_BYTES = DEFAULT_STREAM_ARGS_BYTES


def _extract_artifact(result: Any) -> dict[str, Any] | None:
    """Return a small, redacted projection of a tool result's artifact.

    Tool artifacts are intentionally treated as an untrusted side channel. A
    tool can attach arbitrary nested data to ``ToolMessage.artifact``; only a
    small allowlist of scalar fields is exposed on the normal tool-result
    stream. In particular, preserving ``summary`` gives the UI a concise
    explanation without forwarding files, records, or other raw payloads.
    """
    if isinstance(result, Mapping):
        artifact = result.get("artifact")
    else:
        artifact = getattr(result, "artifact", None)
    if not isinstance(artifact, Mapping):
        return None

    projection: dict[str, Any] = {}
    for field in _ARTIFACT_FIELDS:
        value = artifact.get(field)
        if field == "summary":
            if isinstance(value, str):
                projection[field] = truncate_text(redact_secrets(value), _ARTIFACT_STRING_BYTES)
            continue

        if isinstance(value, bool) or isinstance(value, int) and abs(value) <= 10**12:
            projection[field] = value
        elif isinstance(value, float) and value == value and abs(value) <= 10**12:
            # Keep only finite, reasonably-sized numbers; NaN is not strict
            # JSON and very large values defeat the bounded projection.
            projection[field] = value
        elif isinstance(value, str):
            projection[field] = truncate_text(redact_secrets(value), _ARTIFACT_STRING_BYTES)

    _append_remaining_file_lines(projection, result)
    if not projection:
        return None
    safe = truncate_json_safe(projection, _ARTIFACT_TOTAL_BYTES)
    if isinstance(safe, dict):
        return safe
    summary = projection.get("summary")
    return {"summary": summary} if isinstance(summary, str) else None


def _append_remaining_file_lines(
    projection: dict[str, Any],
    result: Any,
) -> None:
    """Annotate truncated file summaries with the omitted line count."""
    summary = projection.get("summary")
    count = projection.get("count")
    if (
        not isinstance(summary, str)
        or not summary
        or projection.get("kind") != "file"
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
    ):
        return

    preview = _redact_preview(result)
    if not preview.endswith(TRUNCATION_SUFFIX):
        return

    visible_text = preview[: -len(TRUNCATION_SUFFIX)]
    visible_lines = len(visible_text.splitlines())
    remaining = count - visible_lines
    if remaining > 0:
        projection["summary"] = truncate_text(
            f"{summary} · {remaining} more lines not shown",
            _ARTIFACT_STRING_BYTES,
        )


def _unwrap_command_result(value: Any) -> Any:
    """Pull the underlying tool result out of a ``Command`` state update.

    Subagent (``task``) tool calls -- and any tool that injects private state
    via ``Command(update=...)`` -- return a ``Command``, not a ``ToolMessage``,
    so a naive ``str(value)`` preview shows the object's raw repr (e.g.
    "Command(update={'files': {}, 'messages': [ToolMessage(...)]})") instead
    of the actual text a user would recognize as the tool's output.
    """
    if not isinstance(value, Command):
        return value
    update = value.update
    messages = update.get("messages") if isinstance(update, Mapping) else None
    if isinstance(messages, list) and messages:
        return messages[-1]
    return value


def _result_text(value: Any) -> str:
    """Coerce a tool result (or its message wrapper) to its full text."""
    if value is None:
        return ""
    if hasattr(value, "content"):
        text = value.content
        return text if isinstance(text, str) else str(text)
    return value if isinstance(value, str) else str(value)


def _redact_preview(value: Any) -> str:
    """Coerce ``value`` to a redacted, truncated string preview."""
    text = _result_text(value)
    redacted = redact_secrets(text) if text else ""
    return truncate_text(redacted, DEFAULT_STREAM_PREVIEW_BYTES)


_CORRELATION_METADATA_FIELDS = (
    "tool_call_id",
    "subagent_type",
    "task_id",
    "task_summary",
)


def _normalize_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Redact and bound metadata while preserving correlation fields.

    Tool dispatch metadata can contain a large ``args_preview`` (especially for
    ``task`` calls). Keep the fields used to join tool and subagent lifecycle
    events outside the general metadata byte budget so truncating the payload
    cannot break that correlation or discard the bounded task summary. All
    remaining fields retain the existing ``truncate_json_safe`` behavior and
    budget.
    """
    redacted = _redact_value(dict(metadata) if metadata is not None else {})
    if not isinstance(redacted, dict):
        return {"value": truncate_json_safe(redacted, DEFAULT_STREAM_ARGS_BYTES)}

    correlation = {
        field: redacted[field] for field in _CORRELATION_METADATA_FIELDS if field in redacted
    }
    payload = {
        field: value
        for field, value in redacted.items()
        if field not in _CORRELATION_METADATA_FIELDS
    }
    safe_payload = truncate_json_safe(payload, DEFAULT_STREAM_ARGS_BYTES)
    if isinstance(safe_payload, dict):
        return {**correlation, **safe_payload}
    return {**correlation, "value": safe_payload}
