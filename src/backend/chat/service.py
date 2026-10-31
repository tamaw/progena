"""Backend chat session read model backed by ``session:`` documents.

The sidebar/list/detail read ``progena.state`` / ``session:<thread_id>``
documents created by Postgres.  Messages still come from checkpoints through
the runtime reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.backend.integrations.runtime_history import RuntimeUnavailable
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    CheckpointThreadSnapshot,
)
from src.progena.a2a.payloads import (
    _MAX_PROGRESS_LOG_ENTRIES as _REPLAYED_PROGRESS_LOG_ENTRY_LIMIT,
)
from src.progena.a2a.payloads import (
    _MAX_PROGRESS_MESSAGE_TEXT_LENGTH as _REPLAYED_PROGRESS_MESSAGE_TEXT_LIMIT,
)
from src.progena.documents.kinds import SESSION_KEY_PREFIX
from src.progena.schedules.dispatch import SCHEDULED_INPUT_PREFIX
from src.progena.security.redaction import redact_secrets
from src.progena.sessions.messages import (
    extract_cache_read_token_count,
    extract_context_usage,
    extract_display_metadata,
    extract_message_text,
    extract_output_token_count,
    extract_reasoning_token_count,
    extract_reasoning_trace,
    extract_response_status,
    tool_result_failed,
)
from src.progena.sessions.models import ResponseStatus
from src.progena.sessions.titles import ATTACHMENT_CONTEXT_MARKER, display_title
from src.progena.utils import normalize_utc_timestamp, parse_provider_timestamp

# History must not replay arbitrary tool artifacts (which may contain raw files,
# images, or credentials). Keep only the SSE-compatible user-facing projection.
_ARTIFACT_STRING_LIMIT = 1024
_ARTIFACT_KEYS = ("summary", "count", "kind", "status")


def _artifact_mapping(artifact: Any) -> dict[str, Any] | None:
    if isinstance(artifact, dict):
        return artifact
    model_dump = getattr(artifact, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except Exception:  # nosec B110 - an optional artifact must never break history
            return None
        return dumped if isinstance(dumped, dict) else None
    return None


def _curated_tool_artifact(artifact: Any) -> dict[str, Any] | None:
    """Return a bounded, JSON-safe, redacted artifact projection."""
    source = _artifact_mapping(artifact)
    if source is None:
        return None
    projection: dict[str, Any] = {}
    for key in _ARTIFACT_KEYS:
        if key not in source:
            continue
        value = source[key]
        if key == "count":
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float) and abs(float(value)) <= 1_000_000_000_000:
                projection[key] = value
            continue
        if isinstance(value, str):
            projection[key] = redact_secrets(value)[:_ARTIFACT_STRING_LIMIT]
    if not projection:
        return None
    try:
        json.dumps(projection, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return projection


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _known_session_timestamp(value: Any) -> str | None:
    """Render persisted server-UTC session fields with one unambiguous offset."""
    if not isinstance(value, str) or not value:
        return None
    return normalize_utc_timestamp(value).replace("+00:00", "Z")


def _provider_timestamp(value: Any) -> str | None:
    """Render an explicitly stamped provider instant in UTC."""
    timestamp = parse_provider_timestamp(value)
    return timestamp.isoformat().replace("+00:00", "Z") if timestamp is not None else None


class ReasoningSegment(BaseModel):
    """One model call's reasoning, kept separate so history can show where it fell."""

    text: str
    created_at: str | None = None


UsageCoverage = Literal["complete", "partial", "unknown"]


class ContextCompaction(BaseModel):
    """One comparable provider-measured context contraction."""

    before_input_tokens: int = Field(ge=0)
    after_input_tokens: int = Field(ge=0)
    freed_input_tokens: int = Field(ge=0)
    measurement: Literal["comparable_provider_footprints"]


class ContextUsage(BaseModel):
    """Latest main-agent model call token footprint and optional attribution."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    profile_name: str | None = None
    deployment: str | None = None
    provider_id: str | None = None
    compaction: ContextCompaction | None = None


class ConversationUsageTotals(BaseModel):
    """Root assistant output accounting from the retained checkpoint transcript."""

    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    output_coverage: UsageCoverage = "unknown"
    reasoning_coverage: UsageCoverage = "unknown"
    # Distinct durable root assistant message IDs represented in this projection.
    assistant_message_count: int = 0
    output_token_message_count: int = 0
    reasoning_token_message_count: int = 0


InterruptionReason = Literal["user_cancelled", "new_message"]


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None
    reasoning_timestamp: str | None = None
    # Aggregate provider-reported output from every root model call in this answer.
    token_count: int | None = None
    # Provider-reported output from the visible terminal model call alone.
    final_call_token_count: int | None = None
    response_status: ResponseStatus = "unknown"
    reasoning_token_count: int = 0
    cache_read_token_count: int = 0
    reasoning_text: str = ""
    # Per-model-call reasoning in order; reasoning_text is their "\n\n" join.
    reasoning_segments: list[ReasoningSegment] = Field(default_factory=list)
    display_agent_name: str | None = None
    display_model_name: str | None = None
    display_tool_calls: list[Any] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    context_usage: ContextUsage | None = None
    # Set on the input turn a schedule injected into this chat; survives reload.
    scheduled: dict[str, str | None] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    interruption_reason: InterruptionReason | None = None


class ConversationSummaryScope(BaseModel):
    kind: Literal["conversation", "subagent"]
    id: str
    label: str | None = None


class ConversationSummaryMarker(BaseModel):
    id: str
    anchor_message_id: str | None = None
    summary: str
    scope: ConversationSummaryScope


class ChatSessionSummary(BaseModel):
    thread_id: str
    title: str
    # Where the conversation started: "ui", "telegram" (a channel) or "api".
    source: str | None = None
    pinned: bool = False
    archived: bool = False
    created_at: str | None = None
    last_activity: str | None = None
    message_count: int = 0
    preview: str = ""
    # The runtime could not be reached to read this chat's messages, so
    # message_count/preview are unknown (not zero) until it is back.
    history_unavailable: bool = False
    # Full root transcript accounting; independent of the visible message page.
    usage_totals: ConversationUsageTotals = Field(default_factory=ConversationUsageTotals)


class ChatInterruption(BaseModel):
    next: list[str] = Field(default_factory=list)
    at: str | None = None


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessage] = Field(default_factory=list)
    summary_markers: list[ConversationSummaryMarker] = Field(default_factory=list)
    has_more_messages: bool = False
    active_request_id: str | None = None
    activity: Literal["delegating", "generating"] | None = None
    interruption: ChatInterruption | None = None


CHAT_MESSAGE_PAGE_SIZE = 50


def paginate_chat_messages(
    messages: list[ChatMessage],
    *,
    limit: int = CHAT_MESSAGE_PAGE_SIZE,
    before_message_id: str | None = None,
) -> tuple[list[ChatMessage], bool]:
    """Slice one page out of a chronological (oldest-first) message list.

    ``before_message_id=None`` returns the newest ``limit`` messages (initial
    hydration). Otherwise returns the ``limit`` messages immediately preceding
    the message with that id (older-page pagination for "load earlier").
    If ``before_message_id`` is not found (e.g. the checkpoint changed under a
    stale cursor), falls back to the oldest ``limit`` messages so the caller
    still makes forward progress instead of erroring.
    """
    if not messages:
        return [], False
    if before_message_id is None:
        end = len(messages)
    else:
        end = next(
            (index for index, message in enumerate(messages) if message.id == before_message_id),
            limit,
        )
    start = max(0, end - limit)
    return messages[start:end], start > 0


class ChatMessagesPageResponse(BaseModel):
    thread_id: str
    messages: list[ChatMessage]
    summary_markers: list[ConversationSummaryMarker] = Field(default_factory=list)
    has_more: bool


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionSummary]
    total: int


class ChatPatchRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


@dataclass(frozen=True)
class ChatSessionPatchResult:
    payload: dict[str, Any]
    changed: bool


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(part for part in parts if part)
    if value is None:
        return ""
    return str(value)


def _strip_attachment_context(text: str) -> str:
    """Drop the machine-only attachment note appended by
    ``_resolve_message_attachments`` (see ``ATTACHMENT_CONTEXT_MARKER``'s
    doc comment) so the persisted/displayed chat text is exactly what the
    user typed, not the extra pointer note the LLM needed to notice the
    upload.
    """
    marker_index = text.find(ATTACHMENT_CONTEXT_MARKER)
    return text[:marker_index].rstrip() if marker_index != -1 else text


def _extract_images_from_content(content: Any) -> list[dict[str, str]]:
    """Extract image items from ToolMessage content (list of content blocks)."""
    if not isinstance(content, list):
        return []
    images = []
    for item in content:
        if not isinstance(item, dict):
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


def _extract_progress_log_from_content(content: Any) -> list[dict[str, str]]:
    """Pull a2a_send's compact `progress_log` from a ToolMessage result before
    `result_text` truncates it. `build_task_response` keeps the key last, so a
    naive 400-character result preview cannot retain it."""
    if not isinstance(content, str) or not content.strip():
        return []
    if '"progress_log"' not in content:
        return []
    try:
        parsed = json.loads(content)
    except (ValueError, RecursionError):
        return []
    if not isinstance(parsed, dict):
        return []
    raw_log = parsed.get("progress_log")
    if not isinstance(raw_log, list):
        return []
    entries: list[dict[str, str]] = []
    for item in raw_log:
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "")
        message_text = str(item.get("message_text") or "")
        if len(message_text) > _REPLAYED_PROGRESS_MESSAGE_TEXT_LIMIT:
            message_text = message_text[:_REPLAYED_PROGRESS_MESSAGE_TEXT_LIMIT] + "…"
        agent_name = str(item.get("agent_name") or "")
        timestamp = str(item.get("timestamp") or "")
        if state or message_text:
            entries.append(
                {
                    "state": state,
                    "message_text": message_text,
                    "agent_name": agent_name,
                    "timestamp": timestamp,
                }
            )
    if len(entries) > _REPLAYED_PROGRESS_LOG_ENTRY_LIMIT:
        entries = entries[-_REPLAYED_PROGRESS_LOG_ENTRY_LIMIT:]
    return entries


def _raw_message_id(raw: Any, index: int) -> str:
    raw_id = raw.get("id") if isinstance(raw, dict) else getattr(raw, "id", None)
    return str(raw_id or f"message_{index}")


def _raw_message_role(raw: Any) -> str:
    if isinstance(raw, dict):
        role = str(raw.get("role") or raw.get("type") or "assistant").lower()
    else:
        role = str(getattr(raw, "role", "") or getattr(raw, "type", "") or "assistant").lower()
    if role in {"human", "user"}:
        return "user"
    if role in {"ai", "assistant"}:
        return "assistant"
    return role or "assistant"


def _durable_raw_message_id(raw: Any) -> str | None:
    """Return a persisted LangChain message ID without inventing a page-local one."""
    raw_id = raw.get("id") if isinstance(raw, dict) else getattr(raw, "id", None)
    return str(raw_id) if isinstance(raw_id, str) and raw_id else None


def _usage_metric_total(
    values: list[int | None], *, unidentifiable_assistant_count: int
) -> tuple[int | None, UsageCoverage, int]:
    known_values = [value for value in values if value is not None]
    known_count = len(known_values)
    if not values and not unidentifiable_assistant_count:
        return 0, "complete", 0
    if known_count == len(values) and not unidentifiable_assistant_count:
        return sum(known_values), "complete", known_count
    if known_count:
        return sum(known_values), "partial", known_count
    return None, "unknown", 0


def _usage_totals_from_checkpoint(
    snapshot: CheckpointThreadSnapshot | None,
) -> ConversationUsageTotals:
    """Account for the full retained root transcript before any page is sliced.

    Native conversation summaries retain the root LangChain messages.  The
    checkpoint projection is therefore the canonical full-session source; it
    intentionally excludes subagent namespaces and does not walk historical
    checkpoint revisions.  A durable ID is required so repeated persisted
    emissions cannot inflate the totals.
    """
    if snapshot is None or not snapshot.messages_available:
        return ConversationUsageTotals()

    usage_by_message_id: dict[str, tuple[int | None, int | None]] = {}
    unidentifiable_assistant_count = 0
    for raw in snapshot.messages:
        if _raw_message_role(raw) != "assistant":
            continue
        message_id = _durable_raw_message_id(raw)
        if message_id is None:
            unidentifiable_assistant_count += 1
            continue
        previous_output, previous_reasoning = usage_by_message_id.get(message_id, (None, None))
        output_tokens = extract_output_token_count(raw)
        reasoning_tokens = extract_reasoning_token_count(raw)
        usage_by_message_id[message_id] = (
            output_tokens if output_tokens is not None else previous_output,
            reasoning_tokens if reasoning_tokens is not None else previous_reasoning,
        )

    output_tokens, output_coverage, output_count = _usage_metric_total(
        [usage[0] for usage in usage_by_message_id.values()],
        unidentifiable_assistant_count=unidentifiable_assistant_count,
    )
    reasoning_tokens, reasoning_coverage, reasoning_count = _usage_metric_total(
        [usage[1] for usage in usage_by_message_id.values()],
        unidentifiable_assistant_count=unidentifiable_assistant_count,
    )
    return ConversationUsageTotals(
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        output_coverage=output_coverage,
        reasoning_coverage=reasoning_coverage,
        assistant_message_count=len(usage_by_message_id),
        output_token_message_count=output_count,
        reasoning_token_message_count=reasoning_count,
    )


def _raw_message_created_at(raw: Any) -> str | None:
    if isinstance(raw, dict):
        created_at = raw.get("created_at")
        if created_at is None:
            created_at = raw.get("timestamp")
        if created_at is None:
            created_at = raw.get("time")
        metadata = raw.get("response_metadata")
        extra_kwargs = raw.get("additional_kwargs")
    else:
        created_at = getattr(raw, "created_at", None)
        if created_at is None:
            created_at = getattr(raw, "timestamp", None)
        if created_at is None:
            created_at = getattr(raw, "time", None)
        metadata = getattr(raw, "response_metadata", None)
        extra_kwargs = getattr(raw, "additional_kwargs", None)
    if created_at is None and isinstance(metadata, dict):
        created_at = metadata.get("created_at")
    if created_at is None and isinstance(extra_kwargs, dict):
        created_at = extra_kwargs.get("created_at")
    return _provider_timestamp(created_at)


def _answer_response_status(raw: Any) -> ResponseStatus:
    """Classify a visible final model answer from persisted terminal metadata."""
    return extract_response_status(raw)


def _unfinished_tool_group_status(
    explicit_status: ResponseStatus | None,
    *,
    interrupted_by_next_human: bool,
) -> ResponseStatus:
    """Classify a synthetic group only from a persisted terminal boundary."""
    if explicit_status in {"failed", "cancelled", "interrupted"}:
        return explicit_status
    return "interrupted" if interrupted_by_next_human else "unknown"


def _raw_message_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "model_dump"):
        try:
            dumped = raw.model_dump(mode="json")
            return dict(dumped) if isinstance(dumped, dict) else {}
        except Exception:  # nosec B110
            pass
    return {
        "id": getattr(raw, "id", None),
        "type": getattr(raw, "type", None),
        "role": getattr(raw, "role", None),
        "content": getattr(raw, "content", None),
        "usage_metadata": getattr(raw, "usage_metadata", None),
        "response_metadata": getattr(raw, "response_metadata", None),
    }


def _display_metadata_from_raw(raw: Any) -> dict[str, Any]:
    return extract_display_metadata(raw)


def _scheduled_from_raw(raw: Any) -> dict[str, str | None] | None:
    """Schedule provenance stamped onto a HumanMessage's kwargs, if any."""
    additional = (
        raw.get("additional_kwargs")
        if isinstance(raw, dict)
        else getattr(raw, "additional_kwargs", None)
    )
    scheduled = additional.get("scheduled") if isinstance(additional, dict) else None
    if not isinstance(scheduled, dict):
        return None
    return {
        "schedule_id": scheduled.get("schedule_id"),
        "run_id": scheduled.get("run_id"),
    }


def _attachments_from_raw(raw: Any) -> list[dict[str, Any]]:
    """Extract the ``attachments`` list stamped onto a HumanMessage's kwargs."""
    additional = (
        raw.get("additional_kwargs")
        if isinstance(raw, dict)
        else getattr(raw, "additional_kwargs", None)
    )
    if not isinstance(additional, dict):
        return []
    attachments = additional.get("attachments")
    return (
        [item for item in attachments if isinstance(item, dict)]
        if isinstance(attachments, list)
        else []
    )


def _extract_raw_tool_calls(raw: Any) -> list[dict[str, Any]]:
    """Return tool_calls list from an AIMessage (normalized LangChain format).

    Tool-call-only AIMessages (no visible content) are discarded before they
    ever become a ChatMessage -- see _chat_message_from_raw's `if not content
    and role == "assistant": return None` -- so their created_at would
    otherwise be lost. Stamping it onto each extracted tool call here is what
    lets the frontend reconstruct true chronological order (tool calls vs. the
    answer text they get bundled with) on reload instead of guessing.
    """
    if isinstance(raw, dict):
        tool_calls = raw.get("tool_calls")
    else:
        tool_calls = getattr(raw, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return []
    created_at = _raw_message_created_at(raw)
    result: list[dict[str, Any]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "")
        if not name:
            continue
        result.append(
            {
                "id": str(tc.get("id") or ""),
                "name": name,
                "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
                "created_at": created_at,
            }
        )
    return result


CANCELLED_SUFFIX = " [Cancelled]"


def _without_cancelled_sentinel(content: str) -> str:
    """Drop only the exact suffix emitted by the retired history projection."""
    if content == CANCELLED_SUFFIX.strip():
        return ""
    return content.removesuffix(CANCELLED_SUFFIX)


def _chat_message_from_raw(
    raw: Any,
    index: int,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
) -> ChatMessage | None:
    if isinstance(raw, str):
        return ChatMessage(id=f"message_{index}", role="assistant", content=raw)
    if not isinstance(raw, dict) and not hasattr(raw, "content"):
        return None
    role = _raw_message_role(raw)
    if role == "tool":
        return None
    content = _raw_message_content(raw)
    scheduled = _scheduled_from_raw(raw)
    if scheduled and content.startswith(SCHEDULED_INPUT_PREFIX):
        content = content[len(SCHEDULED_INPUT_PREFIX) :]
    response_status: ResponseStatus = (
        _answer_response_status(raw) if role == "assistant" else "unknown"
    )
    interruption_reason: InterruptionReason | None = None
    if role == "assistant" and response_status == "cancelled":
        content = _without_cancelled_sentinel(content)
        interruption_reason = "user_cancelled"
    reasoning_text = extract_reasoning_trace(raw) or ""
    if not content and role == "assistant":
        if response_status not in {"failed", "cancelled"}:
            return None
        # Retain otherwise empty terminal rows so reload can expose their
        # native status and authoritative usage. Work/reasoning already renders
        # its own terminal status, so only a truly empty failure needs prose.
        if response_status == "failed" and not tool_calls and not reasoning_text:
            content = "Error: Response failed."
    created_at = _raw_message_created_at(raw)
    display_metadata = _display_metadata_from_raw(raw)
    final_call_token_count = extract_output_token_count(raw) if role == "assistant" else None
    return ChatMessage(
        id=_raw_message_id(raw, index),
        role=role,
        content=content,
        created_at=created_at,
        reasoning_timestamp=created_at if reasoning_text else None,
        reasoning_segments=(
            [ReasoningSegment(text=reasoning_text, created_at=created_at)] if reasoning_text else []
        ),
        token_count=extract_output_token_count(raw),
        final_call_token_count=final_call_token_count,
        response_status=response_status,
        interruption_reason=interruption_reason,
        reasoning_token_count=extract_reasoning_token_count(raw) or 0,
        cache_read_token_count=extract_cache_read_token_count(raw) or 0,
        reasoning_text=reasoning_text,
        display_agent_name=display_metadata.get("display_agent_name"),
        display_model_name=display_metadata.get("display_model_name"),
        context_usage=(
            ContextUsage.model_validate(context_usage)
            if (context_usage := extract_context_usage(raw))
            else None
        ),
        display_tool_calls=display_metadata.get("display_tool_calls", []),
        tool_calls=tool_calls or [],
        attachments=_attachments_from_raw(raw),
        scheduled=scheduled,
        raw=_raw_message_payload(raw),
    )


def _raw_message_content(raw: Any) -> str:
    """Return visible text without treating reasoning blocks as an answer."""
    text = extract_message_text(raw)
    if text is None:
        fallback = (
            raw.get("message") or raw.get("text")
            if isinstance(raw, dict)
            else getattr(raw, "message", None) or getattr(raw, "text", None)
        )
        text = _message_text(fallback)
    return _strip_attachment_context(text)


def _tool_message_duration(raw: Any) -> int | float | None:
    """Read a checkpointed tool-call duration from a ToolMessage."""
    additional_kwargs = (
        raw.get("additional_kwargs")
        if isinstance(raw, dict)
        else getattr(raw, "additional_kwargs", None)
    )
    if not isinstance(additional_kwargs, dict):
        return None
    duration_ms = additional_kwargs.get("duration_ms")
    return (
        duration_ms
        if isinstance(duration_ms, int | float) and not isinstance(duration_ms, bool)
        else None
    )


def _synthetic_pending_tool_call_message(
    tool_calls: list[dict[str, Any]], index: int
) -> ChatMessage:
    """A placeholder ChatMessage for tool calls whose owning turn never produced
    trailing text -- e.g. the turn was interrupted (Stop / page reload racing
    the request / dropped connection) right after a tool result landed but
    before the model's follow-up commentary came back. Without this, such tool
    calls (a subagent dispatch, an A2A call, any tool) would be silently
    discarded by `_messages_from_checkpoint` below and the whole turn would
    vanish from history on reload even though it genuinely ran and, usually,
    already has a real result attached.
    """
    created_at = tool_calls[-1].get("created_at") if tool_calls else None
    return ChatMessage(
        id=f"message_{index}_pending_tools",
        role="assistant",
        content="",
        created_at=created_at,
        tool_calls=tool_calls,
    )


def _tool_result_preview(result_text: str) -> str:
    """Preserve both ends of a long historical tool result."""
    preview_edge = 200
    if len(result_text) <= preview_edge * 2:
        return result_text
    omitted = result_text[preview_edge:-preview_edge]
    omitted_kb = max(1, (len(omitted.encode("utf-8")) + 1023) // 1024)
    return f"{result_text[:preview_edge]}… ⟨{omitted_kb} KB omitted⟩ …{result_text[-preview_edge:]}"


def _messages_from_checkpoint(
    snapshot: CheckpointThreadSnapshot | None,
) -> list[ChatMessage]:
    if snapshot is None:
        return []

    tool_results: dict[str, dict[str, Any]] = {}
    for raw in snapshot.messages:
        if _raw_message_role(raw) != "tool":
            continue
        call_id = (
            raw.get("tool_call_id") if isinstance(raw, dict) else getattr(raw, "tool_call_id", None)
        )
        if not call_id:
            continue
        raw_content = (
            raw.get("content", "") if isinstance(raw, dict) else getattr(raw, "content", "")
        )
        images = _extract_images_from_content(raw_content)
        progress_log = _extract_progress_log_from_content(raw_content)
        result_text = _message_text(raw_content)
        result_entry: dict[str, Any] = {
            "result": _tool_result_preview(result_text),
        }
        tool_name = raw.get("name") if isinstance(raw, dict) else getattr(raw, "name", None)
        if tool_result_failed(tool_name, raw, result_text):
            # Classified before truncation; the stored preview may lose the footer.
            result_entry["failed"] = True
        artifact = raw.get("artifact") if isinstance(raw, dict) else getattr(raw, "artifact", None)
        curated_artifact = _curated_tool_artifact(artifact)
        if curated_artifact is not None:
            result_entry["artifact"] = curated_artifact
        if images:
            result_entry["images"] = images
        if progress_log:
            result_entry["progress_log"] = progress_log
        duration_ms = _tool_message_duration(raw)
        if duration_ms is not None:
            result_entry["duration_ms"] = duration_ms
        tool_results[str(call_id)] = result_entry

    messages: list[ChatMessage] = []
    pending_tool_calls: list[dict[str, Any]] = []
    pending_token_count: int | None = 0
    pending_terminal_status: ResponseStatus | None = None
    pending_reasoning_token_count = 0
    pending_cache_read_token_count = 0
    pending_reasoning_parts: list[ReasoningSegment] = []
    pending_reasoning_timestamp: str | None = None
    pending_context_usage: ContextUsage | None = None

    for index, raw in enumerate(snapshot.messages):
        role = _raw_message_role(raw)
        if role == "tool":
            continue

        if role == "user":
            if pending_tool_calls or pending_reasoning_parts:
                synthetic = _synthetic_pending_tool_call_message(pending_tool_calls, index)
                synthetic.reasoning_text = _join_reasoning(pending_reasoning_parts)
                synthetic.reasoning_segments = pending_reasoning_parts
                synthetic.reasoning_timestamp = pending_reasoning_timestamp
                synthetic.token_count = pending_token_count
                synthetic.response_status = _unfinished_tool_group_status(
                    pending_terminal_status, interrupted_by_next_human=True
                )
                synthetic.reasoning_token_count = pending_reasoning_token_count
                synthetic.cache_read_token_count = pending_cache_read_token_count
                synthetic.context_usage = pending_context_usage
                messages.append(synthetic)
                pending_tool_calls = []
            pending_token_count = 0
            pending_terminal_status = None
            pending_reasoning_parts = []
            pending_reasoning_token_count = 0
            pending_cache_read_token_count = 0
            pending_reasoning_timestamp = None
            pending_context_usage = None

        if role == "assistant":
            pending_context_usage = (
                ContextUsage.model_validate(context_usage)
                if (context_usage := extract_context_usage(raw))
                else None
            )
            output_token_count = extract_output_token_count(raw)
            pending_token_count = (
                pending_token_count + output_token_count
                if pending_token_count is not None and output_token_count is not None
                else None
            )
            raw_response_status = extract_response_status(raw)
            if raw_response_status in {"failed", "cancelled", "interrupted"}:
                pending_terminal_status = raw_response_status
            pending_reasoning_token_count += extract_reasoning_token_count(raw) or 0
            pending_cache_read_token_count = (
                extract_cache_read_token_count(raw) or pending_cache_read_token_count
            )
            reasoning_text = extract_reasoning_trace(raw)
            if reasoning_text:
                pending_reasoning_parts.append(
                    ReasoningSegment(text=reasoning_text, created_at=_raw_message_created_at(raw))
                )
                if pending_reasoning_timestamp is None:
                    pending_reasoning_timestamp = _raw_message_created_at(raw)
            raw_tool_calls = _extract_raw_tool_calls(raw)
            # Text the model wrote alongside its tool calls narrates them rather
            # than answering, so it travels with the first call as commentary.
            commentary = _raw_message_content(raw).strip() if raw_tool_calls else ""
            for call_index, tc in enumerate(raw_tool_calls):
                entry = tool_results.get(tc["id"], {})
                tc_with_result = {**tc, "result": entry.get("result", "")}
                if commentary and call_index == 0:
                    tc_with_result["commentary"] = commentary
                if "artifact" in entry:
                    tc_with_result["artifact"] = entry["artifact"]
                if "images" in entry:
                    tc_with_result["images"] = entry["images"]
                if "progress_log" in entry:
                    tc_with_result["progress_log"] = entry["progress_log"]
                if entry.get("failed"):
                    tc_with_result["failed"] = True
                if "duration_ms" in entry:
                    tc_with_result["duration_ms"] = entry["duration_ms"]
                pending_tool_calls.append(tc_with_result)
            if commentary:
                # The narration now lives on its tool call; the message adds nothing.
                continue

        tool_calls_to_attach = list(pending_tool_calls) if role == "assistant" else None
        message = _chat_message_from_raw(raw, index, tool_calls=tool_calls_to_attach)
        if message is not None:
            if role == "assistant":
                if any(call["id"] not in tool_results for call in pending_tool_calls):
                    message.final_call_token_count = None
                message.reasoning_text = _join_reasoning(pending_reasoning_parts)
                message.reasoning_segments = pending_reasoning_parts
                message.token_count = pending_token_count
                message.reasoning_token_count = pending_reasoning_token_count
                message.cache_read_token_count = pending_cache_read_token_count
                message.reasoning_timestamp = pending_reasoning_timestamp
                message.context_usage = pending_context_usage
                pending_token_count = 0
                pending_terminal_status = None
                pending_reasoning_parts = []
                pending_reasoning_token_count = 0
                pending_cache_read_token_count = 0
                pending_reasoning_timestamp = None
                pending_context_usage = None
            if role in ("assistant", "user"):
                pending_tool_calls = []
            messages.append(message)

    if pending_tool_calls or pending_reasoning_parts:
        synthetic = _synthetic_pending_tool_call_message(pending_tool_calls, len(snapshot.messages))
        synthetic.token_count = pending_token_count
        synthetic.response_status = _unfinished_tool_group_status(
            pending_terminal_status, interrupted_by_next_human=False
        )
        synthetic.reasoning_token_count = pending_reasoning_token_count
        synthetic.cache_read_token_count = pending_cache_read_token_count
        synthetic.reasoning_text = _join_reasoning(pending_reasoning_parts)
        synthetic.reasoning_segments = pending_reasoning_parts
        synthetic.reasoning_timestamp = pending_reasoning_timestamp
        synthetic.context_usage = pending_context_usage
        messages.append(synthetic)

    return messages


def _summary_markers_from_checkpoint(
    snapshot: CheckpointThreadSnapshot | None, messages: list[ChatMessage]
) -> list[ConversationSummaryMarker]:
    if snapshot is None:
        return []
    markers: list[ConversationSummaryMarker] = []
    visible_by_id = {message.id: message for message in messages}
    for occurrence in snapshot.summary_occurrences:
        scope = occurrence.scope
        if scope["kind"] == "conversation":
            anchor_message_id = _root_summary_anchor(
                snapshot.messages,
                messages,
                occurrence.created_at_index,
                occurrence.anchor_message_id,
            )
            # A non-head root summary without a visible predecessor cannot be
            # placed honestly. Do not turn it into a false transcript-head marker.
            if anchor_message_id is None and occurrence.created_at_index:
                continue
        else:
            anchor_message_id = _tool_call_anchor(scope["id"], messages)
            if anchor_message_id is None and occurrence.anchor_message_id in visible_by_id:
                anchor_message_id = occurrence.anchor_message_id
        markers.append(
            ConversationSummaryMarker(
                id=occurrence.id,
                anchor_message_id=anchor_message_id,
                summary=occurrence.summary,
                scope=ConversationSummaryScope.model_validate(scope),
            )
        )
    return markers


def _root_summary_anchor(
    raw_messages: list[Any],
    messages: list[ChatMessage],
    created_at_index: int,
    source_anchor_message_id: str | None,
) -> str | None:
    visible_by_id = {message.id: message for message in messages}
    upper_bound = min(created_at_index, len(raw_messages))
    for index in range(upper_bound - 1, -1, -1):
        raw = raw_messages[index]
        raw_id = _raw_message_id(raw, index)
        if raw_id in visible_by_id:
            return raw_id
        for tool_call in _extract_raw_tool_calls(raw):
            if anchor := _tool_call_anchor(tool_call["id"], messages):
                return anchor
    return source_anchor_message_id if source_anchor_message_id in visible_by_id else None


def _tool_call_anchor(tool_call_id: str, messages: list[ChatMessage]) -> str | None:
    for message in messages:
        if any(call.get("id") == tool_call_id for call in message.tool_calls):
            return message.id
    return None


def summary_markers_for_page(
    markers: list[ConversationSummaryMarker],
    messages: list[ChatMessage],
    *,
    includes_transcript_head: bool,
) -> list[ConversationSummaryMarker]:
    """Return only markers whose creation boundary is present in this page."""
    visible_ids = {message.id for message in messages}
    return [
        marker
        for marker in markers
        if marker.anchor_message_id in visible_ids
        or marker.anchor_message_id is None
        and includes_transcript_head
    ]


def _join_reasoning(segments: list[ReasoningSegment]) -> str:
    return "\n\n".join(segment.text for segment in segments)


def _summary_from_session(
    payload: dict[str, Any],
    *,
    messages: list[ChatMessage],
    usage_totals: ConversationUsageTotals,
    history_unavailable: bool = False,
) -> ChatSessionSummary:
    preview = next((message.content for message in reversed(messages) if message.content), "")
    return ChatSessionSummary(
        thread_id=payload["thread_id"],
        title=display_title(payload["title"], payload["first_message"]),
        source=payload.get("source"),
        pinned=payload["pinned"],
        archived=payload["archived"],
        created_at=_known_session_timestamp(payload.get("created_at")),
        last_activity=_known_session_timestamp(payload.get("last_activity")),
        message_count=len(messages),
        preview=preview,
        history_unavailable=history_unavailable,
        usage_totals=usage_totals,
    )


def _session_payload(store: BackendDocumentStore, thread_id: str) -> dict[str, Any] | None:
    document = store.get_document(PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}")
    return None if document is None else dict(document.payload)


def list_chat_sessions(
    store: BackendDocumentStore,
    *,
    include_archived: bool = False,
) -> list[ChatSessionSummary]:
    sessions: list[ChatSessionSummary] = []
    checkpoint_snapshots = store.list_checkpoint_thread_snapshots(limit=100)
    snapshots_by_thread_id = {snapshot.thread_id: snapshot for snapshot in checkpoint_snapshots}

    for _key, document in store.list_documents(PROGENA_STATE_PREFIX, SESSION_KEY_PREFIX):
        payload = document.payload
        thread_id = payload["thread_id"]
        if payload["archived"] and not include_archived:
            continue
        try:
            snapshot = snapshots_by_thread_id.get(thread_id)
            if snapshot is None:
                snapshot = store.get_checkpoint_thread_snapshot(thread_id)
            history_unavailable = snapshot is not None and not snapshot.messages_available
        except RuntimeUnavailable:
            snapshot = None
            history_unavailable = True
        messages = _messages_from_checkpoint(snapshot)
        sessions.append(
            _summary_from_session(
                payload,
                messages=messages,
                usage_totals=_usage_totals_from_checkpoint(snapshot),
                history_unavailable=history_unavailable,
            )
        )

    sessions.sort(key=lambda item: (item.pinned, item.last_activity or ""), reverse=True)
    return sessions


def get_chat_detail(
    store: BackendDocumentStore,
    thread_id: str,
) -> ChatSessionDetail | None:
    payload = _session_payload(store, thread_id)
    if payload is None:
        return None
    snapshot = store.get_checkpoint_thread_snapshot(thread_id, include_summary_occurrences=True)
    messages = _messages_from_checkpoint(snapshot)
    summary_markers = _summary_markers_from_checkpoint(snapshot, messages)
    summary = _summary_from_session(
        payload,
        messages=messages,
        usage_totals=_usage_totals_from_checkpoint(snapshot),
    )
    return ChatSessionDetail(
        **summary.model_dump(), messages=messages, summary_markers=summary_markers
    )


def get_chat_summary(store: BackendDocumentStore, thread_id: str) -> ChatSessionSummary | None:
    """The sidebar summary of one session, with history read best-effort.

    Used by the document-event handler: a runtime outage marks the history
    unavailable (as the roster does) instead of failing event dispatch.
    """
    payload = _session_payload(store, thread_id)
    if payload is None:
        return None
    try:
        snapshot = store.get_checkpoint_thread_snapshot(thread_id)
        history_unavailable = snapshot is not None and not snapshot.messages_available
    except RuntimeUnavailable:
        snapshot = None
        history_unavailable = True
    messages = _messages_from_checkpoint(snapshot)
    return _summary_from_session(
        payload,
        messages=messages,
        usage_totals=_usage_totals_from_checkpoint(snapshot),
        history_unavailable=history_unavailable,
    )


def patch_session_document(
    store: BackendDocumentStore,
    thread_id: str,
    request: ChatPatchRequest,
) -> ChatSessionPatchResult | None:
    payload = _session_payload(store, thread_id)
    if payload is None:
        return None
    updates: dict[str, Any] = {}
    if request.title is not None:
        title = request.title.strip()
        if title:
            updates["title"] = title
    if request.pinned is not None:
        updates["pinned"] = request.pinned
    if request.archived is not None:
        updates["archived"] = request.archived
    if not updates:
        return ChatSessionPatchResult(payload=payload, changed=False)
    if not store.merge_document_if_changed(
        PROGENA_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}", updates
    ):
        current = _session_payload(store, thread_id)
        return None if current is None else ChatSessionPatchResult(payload=current, changed=False)
    return ChatSessionPatchResult(payload={**payload, **updates}, changed=True)


def delete_session_document(store: BackendDocumentStore, thread_id: str) -> bool:
    key = f"{SESSION_KEY_PREFIX}{thread_id}"
    existing = store.get_document(PROGENA_STATE_PREFIX, key)
    if existing is None:
        return False
    store.delete_document(PROGENA_STATE_PREFIX, key)
    return True
