"""Message coercion and response extraction helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from src.progena.sessions.models import ResponseStatus

# deepagents' execute footer; a truncation note may follow it, and offloaded
# output puts it first, so match a whole line rather than the end of text.
_EXECUTE_FAILURE = re.compile(r"^\[Command failed with exit code -?\d+\]$", re.MULTILINE)
# langchain_quickjs renders a thrown error as a top-level element.
_JAVASCRIPT_ERROR = re.compile(r'^<error type="', re.MULTILINE)


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


def extract_response_status(msg: Any) -> ResponseStatus:
    """Normalize persisted terminal provider state without inferring current activity."""
    if isinstance(msg, dict):
        raw_mapping = msg
        additional_kwargs = msg.get("additional_kwargs")
    else:
        raw_mapping = {}
        additional_kwargs = getattr(msg, "additional_kwargs", None)
    response_metadata = (
        msg.get("response_metadata")
        if isinstance(msg, dict)
        else getattr(msg, "response_metadata", None)
    )
    containers = (
        response_metadata if isinstance(response_metadata, dict) else {},
        additional_kwargs if isinstance(additional_kwargs, dict) else {},
        raw_mapping,
    )
    for container in containers:
        status = container.get("status")
        if isinstance(status, str):
            normalized = status.strip().lower().replace("_", "-")
            if normalized in {"failed", "failure", "error", "errored", "rejected"}:
                return "failed"
            if normalized in {"cancelled", "canceled", "cancel", "aborted"}:
                return "cancelled"
            if normalized in {"interrupted", "interrupt", "stopped", "incomplete"}:
                return "interrupted"
            if normalized in {"completed", "complete", "succeeded", "success", "finished"}:
                return "completed"
        finish_reason = container.get("finish_reason")
        if isinstance(finish_reason, str):
            normalized = finish_reason.strip().lower().replace("_", "-")
            if normalized in {"failed", "failure", "error", "content-filter"}:
                return "failed"
            if normalized in {"cancelled", "canceled", "cancel", "aborted"}:
                return "cancelled"
            if normalized in {"interrupted", "interrupt", "incomplete"}:
                return "interrupted"
            if normalized in {"stop", "stopped", "length", "completed", "complete"}:
                return "completed"
    return "unknown"


def attach_reasoning_trace(
    msg: Any,
    reasoning_trace: str | None,
    *,
    reasoning_tokens: int | None = None,
) -> None:
    """Attach reasoning metadata to a message when the provider omitted it."""
    trace = (reasoning_trace or "").strip()
    if msg is None or not trace or extract_reasoning_trace(msg):
        return

    if isinstance(msg, dict):
        additional = msg.get("additional_kwargs")
        if not isinstance(additional, dict):
            additional = {}
            msg["additional_kwargs"] = additional
        additional["reasoning"] = {"summary": trace}

        metadata = msg.get("response_metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            msg["response_metadata"] = metadata
        if reasoning_tokens is not None and metadata.get("reasoning_tokens") is None:
            metadata["reasoning_tokens"] = reasoning_tokens
        return

    additional = getattr(msg, "additional_kwargs", None)
    if not isinstance(additional, dict):
        additional = {}
        msg.additional_kwargs = additional
    additional["reasoning"] = {"summary": trace}

    metadata = getattr(msg, "response_metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
        msg.response_metadata = metadata
    if reasoning_tokens is not None and metadata.get("reasoning_tokens") is None:
        metadata["reasoning_tokens"] = reasoning_tokens


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


def tool_result_failed(tool_name: Any, message: Any, text: str) -> bool:
    """Whether a returned tool result reports failure.

    Callers pass the full result text, before any preview truncation, so a long
    failing command keeps its classification after its footer is cut off.
    """
    getter = message.get if isinstance(message, dict) else lambda key: getattr(message, key, None)
    if getter("status") == "error":
        return True
    artifact = getter("artifact")
    exit_code = artifact.get("exit_code") if isinstance(artifact, dict) else None
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return True
    if tool_name == "execute":
        return bool(_EXECUTE_FAILURE.search(text))
    if tool_name == "javascript":
        return bool(_JAVASCRIPT_ERROR.search(text))
    return False


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
            if text := _coerce_reasoning_summary_text(candidate):
                parts.append(text)
        if parts:
            return "\n".join(parts)

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
        for key in ("reasoning", "thinking"):
            if key in container and (text := _coerce_reasoning_summary_text(container.get(key))):
                return text
        if ("summary" in container or "effort" in container) and (
            text := _coerce_reasoning_summary_text(container.get("summary"))
        ):
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


def extract_token_usage(msg: Any) -> dict[str, int] | None:
    """Extract normalized input, output, and total usage from a model message."""
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
        usage = _extract_token_usage_from_container(container)
        if usage is not None:
            return usage

    return None


def extract_output_token_count(msg: Any) -> int | None:
    """Read explicit provider output usage; total-only usage is not output."""
    for key in ("usage_metadata", "response_metadata"):
        container = msg.get(key) if isinstance(msg, dict) else getattr(msg, key, None)
        if isinstance(container, dict):
            count = _output_tokens_from_container(container)
            if count is not None:
                return count
    return None


def _output_tokens_from_container(container: dict[str, Any]) -> int | None:
    for key in ("output_tokens", "completion_tokens"):
        count = container.get(key)
        if type(count) is int and count >= 0:
            return count
    for key in ("usage", "token_usage"):
        nested = container.get(key)
        if isinstance(nested, dict):
            count = _output_tokens_from_container(nested)
            if count is not None:
                return count
    return None


def extract_context_usage(msg: Any) -> dict[str, int | str] | None:
    """Return one complete model call's usage and persisted profile attribution.

    Context capacity is meaningful only for an actual provider call. Unlike
    aggregate accounting, this deliberately rejects partial usage records
    rather than inventing zero input or output tokens.
    """
    if msg is None:
        return None

    containers: list[dict[str, Any]] = []
    if isinstance(msg, dict):
        for key in ("usage_metadata", "response_metadata"):
            value = msg.get(key)
            if isinstance(value, dict):
                containers.append(value)
        response_metadata = msg.get("response_metadata")
    else:
        for attr in ("usage_metadata", "response_metadata"):
            value = getattr(msg, attr, None)
            if isinstance(value, dict):
                containers.append(value)
        response_metadata = getattr(msg, "response_metadata", None)

    usage = next(
        (
            extracted
            for container in containers
            if (extracted := _extract_complete_token_usage_from_container(container)) is not None
        ),
        None,
    )
    if usage is None:
        return None

    result: dict[str, int | str] = dict(usage)
    if not isinstance(response_metadata, dict):
        return result
    insights = response_metadata.get("insights")
    if isinstance(insights, dict):
        for key in ("profile_name", "deployment", "provider_id"):
            value = insights.get(key)
            if isinstance(value, str) and value:
                result[key] = value
    if "provider_id" not in result:
        provider = response_metadata.get("progena_provider")
        if isinstance(provider, dict):
            provider_id = provider.get("id")
            if isinstance(provider_id, str) and provider_id:
                result["provider_id"] = provider_id
    return result


def extract_cache_read_token_count(msg: Any) -> int | None:
    """Extract Azure's reported prompt-cache-read token count from a completed message.

    Looks under ``input_token_details.cache_read`` (the LangChain
    ``responses/v1`` output shape), mirroring
    ``extract_reasoning_token_count``'s container-search pattern.
    """
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
        token_count = _extract_cache_read_token_count_from_container(container)
        if token_count is not None:
            return token_count

    return None


def extract_display_metadata(msg: Any) -> dict[str, Any]:
    """Extract display metadata fields from an AI message's response_metadata.

    Returns a dict with the existing keys ``display_agent_name``,
    ``display_model_name``, and ``display_tool_calls`` when present.
    Used by both checkpoint loading (chat_sessions) and live streaming
    (deepagent_stream) so they share one metadata contract.
    """
    if msg is None:
        return {}
    metadata = (
        msg.get("response_metadata")
        if isinstance(msg, dict)
        else getattr(msg, "response_metadata", None)
    )
    if not isinstance(metadata, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("display_agent_name", "display_model_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    tool_calls = metadata.get("display_tool_calls")
    if isinstance(tool_calls, list):
        result["display_tool_calls"] = tool_calls
    return result


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


def _extract_complete_token_usage_from_container(
    container: dict[str, Any],
) -> dict[str, int] | None:
    """Extract a provider footprint only when both input and output are known."""
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    usage: dict[str, int] = {}
    for normalized, keys in aliases.items():
        for key in keys:
            value = container.get(key)
            if type(value) is int and value >= 0:
                usage[normalized] = value
                break
    if "input_tokens" in usage and "output_tokens" in usage:
        return {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage.get(
                "total_tokens", usage["input_tokens"] + usage["output_tokens"]
            ),
        }
    for key in ("token_usage", "usage"):
        nested = container.get(key)
        if isinstance(nested, dict):
            nested_usage = _extract_complete_token_usage_from_container(nested)
            if nested_usage is not None:
                return nested_usage
    return None


def _extract_token_usage_from_container(container: dict[str, Any]) -> dict[str, int] | None:
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    usage: dict[str, int] = {}
    for normalized, keys in aliases.items():
        for key in keys:
            value = container.get(key)
            if type(value) is int and value >= 0:
                usage[normalized] = value
                break

    if usage:
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": usage.get("total_tokens", input_tokens + output_tokens),
        }

    for key in ("token_usage", "usage"):
        nested = container.get(key)
        if not isinstance(nested, dict):
            continue
        nested_usage = _extract_token_usage_from_container(nested)
        if nested_usage is not None:
            return nested_usage

    return None


def _extract_cache_read_token_count_from_container(container: dict[str, Any]) -> int | None:
    detail_candidates = [
        container.get("input_token_details"),
        container.get("input_tokens_details"),
        container.get("prompt_tokens_details"),
        container.get("token_usage"),
        container.get("usage"),
    ]
    for details in detail_candidates:
        if not isinstance(details, dict):
            continue
        token_count = _extract_cache_read_token_count_from_container(details)
        if token_count is not None:
            return token_count

    for key in ("cache_read", "cached_tokens", "cache_read_tokens"):
        value = container.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)

    return None


__all__ = [
    "attach_reasoning_trace",
    "coerce_messages_list",
    "extract_cache_read_token_count",
    "extract_context_usage",
    "extract_display_metadata",
    "extract_message_text",
    "extract_reasoning_token_count",
    "extract_reasoning_trace",
    "extract_response_status",
    "extract_token_usage",
    "get_last_ai_message",
    "summarize_message_schema",
]
