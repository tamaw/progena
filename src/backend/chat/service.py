"""Backend chat session read model backed by ``session:`` documents.

The sidebar/list/detail read ``progenitor.state`` / ``session:<thread_id>``
documents created by Postgres.  Messages still come from checkpoints through
the runtime reader.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from src.backend.integrations.runtime_history import RuntimeUnavailable
from src.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    CheckpointThreadSnapshot,
)
from src.progenitor.a2a.payloads import (
    _MAX_PROGRESS_LOG_ENTRIES as _REPLAYED_PROGRESS_LOG_ENTRY_LIMIT,
)
from src.progenitor.a2a.payloads import (
    _MAX_PROGRESS_MESSAGE_TEXT_LENGTH as _REPLAYED_PROGRESS_MESSAGE_TEXT_LIMIT,
)
from src.progenitor.documents.kinds import SESSION_KEY_PREFIX
from src.progenitor.security.redaction import redact_secrets
from src.progenitor.sessions.messages import (
    extract_cache_read_token_count,
    extract_display_metadata,
    extract_message_text,
    extract_reasoning_token_count,
    extract_reasoning_trace,
)
from src.progenitor.sessions.titles import ATTACHMENT_CONTEXT_MARKER, display_title

# History must not replay arbitrary tool artifacts (which may contain raw files,
# images, or credentials). Keep only the SSE-compatible user-facing projection.
_ARTIFACT_STRING_LIMIT = 1024
_ARTIFACT_KEYS = ("summary", "count", "kind", "status")


def _tool_call_audit_index(store: BackendDocumentStore | None) -> dict[str, dict[str, Any]]:
    """Read one bounded audit-log page and index normal tool calls by id."""
    if store is None:
        return {}
    query_logs = getattr(store, "query_logs", None)
    if not callable(query_logs):
        return {}
    try:
        rows = query_logs(source="tool_calls", limit=500)
    except Exception:  # nosec B110 - old/test stores may not expose audit logs
        return {}
    if not isinstance(rows, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_payload = row.get("payload")
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)
            for key in ("tool_id", "duration_ms"):
                if key not in payload and key in row:
                    payload[key] = row[key]
        else:
            payload = row
        tool_id = payload.get("tool_id")
        if tool_id is None:
            tool_id = row.get("tool_id")
        if tool_id is None:
            continue
        indexed[str(tool_id)] = payload
    return indexed


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


SCHEDULED_TASK_PREFIX = "[Scheduled Task]"
BACKEND_ALERT_PREFIX = "A backend alert was just created for the user."


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None
    reasoning_timestamp: str | None = None
    token_count: int = 0
    reasoning_token_count: int = 0
    cache_read_token_count: int = 0
    reasoning_text: str = ""
    display_agent_name: str | None = None
    display_model_name: str | None = None
    display_tool_calls: list[Any] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatSessionSummary(BaseModel):
    thread_id: str
    title: str
    pinned: bool = False
    archived: bool = False
    created_at: str | None = None
    last_activity: str | None = None
    message_count: int = 0
    preview: str = ""
    # The runtime could not be reached to read this chat's messages, so
    # message_count/preview are unknown (not zero) until it is back.
    history_unavailable: bool = False


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessage] = Field(default_factory=list)
    has_more_messages: bool = False
    active_request_id: str | None = None


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
    has_more: bool


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionSummary]
    total: int


class ChatPatchRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ChatMutationResponse(BaseModel):
    session: ChatSessionDetail


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


def _raw_message_created_at(raw: Any) -> str | None:
    if isinstance(raw, dict):
        created_at = raw.get("created_at") or raw.get("timestamp") or raw.get("time")
        metadata = raw.get("response_metadata")
        extra_kwargs = raw.get("additional_kwargs")
    else:
        created_at = (
            getattr(raw, "created_at", None)
            or getattr(raw, "timestamp", None)
            or getattr(raw, "time", None)
        )
        metadata = getattr(raw, "response_metadata", None)
        extra_kwargs = getattr(raw, "additional_kwargs", None)
    if created_at is None and isinstance(metadata, dict):
        created_at = metadata.get("created_at")
    if created_at is None and isinstance(extra_kwargs, dict):
        created_at = extra_kwargs.get("created_at")
    if isinstance(created_at, int | float):
        return datetime.fromtimestamp(float(created_at), UTC).isoformat().replace("+00:00", "Z")
    return str(created_at) if created_at else None


def _raw_message_token_count(raw: Any) -> int:
    usage = (
        raw.get("usage_metadata") if isinstance(raw, dict) else getattr(raw, "usage_metadata", None)
    )
    if not isinstance(usage, dict):
        return 0
    value = (
        usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("total_tokens")
    )
    return int(value) if isinstance(value, int | float) else 0


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


def _is_cancelled_reply(raw: Any) -> bool:
    """An assistant reply the runtime saved from a cancelled turn."""
    metadata = (
        raw.get("response_metadata")
        if isinstance(raw, dict)
        else getattr(raw, "response_metadata", None)
    )
    return isinstance(metadata, dict) and metadata.get("status") == "cancelled"


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
    if isinstance(raw, dict):
        content = _strip_attachment_context(
            _message_text(raw.get("content") or raw.get("message") or raw.get("text"))
        )
    else:
        content = _strip_attachment_context(
            extract_message_text(raw) or _message_text(getattr(raw, "content", ""))
        )
    if role == "assistant" and _is_cancelled_reply(raw):
        content = f"{content}{CANCELLED_SUFFIX}" if content else CANCELLED_SUFFIX.strip()
    if not content and role == "assistant":
        return None
    reasoning_text = extract_reasoning_trace(raw) or ""
    return ChatMessage(
        id=_raw_message_id(raw, index),
        role=role,
        content=content,
        created_at=_raw_message_created_at(raw),
        reasoning_timestamp=_raw_message_created_at(raw) if reasoning_text else None,
        token_count=_raw_message_token_count(raw),
        reasoning_token_count=extract_reasoning_token_count(raw) or 0,
        cache_read_token_count=extract_cache_read_token_count(raw) or 0,
        reasoning_text=reasoning_text,
        display_agent_name=_display_metadata_from_raw(raw).get("display_agent_name"),
        display_model_name=_display_metadata_from_raw(raw).get("display_model_name"),
        display_tool_calls=_display_metadata_from_raw(raw).get("display_tool_calls", []),
        tool_calls=tool_calls or [],
        attachments=_attachments_from_raw(raw),
        raw=_raw_message_payload(raw),
    )


def _messages_from_state(state: dict[str, Any]) -> list[ChatMessage]:
    raw_messages = state.get("messages")
    if not isinstance(raw_messages, list):
        history = state.get("history")
        raw_messages = history.get("messages") if isinstance(history, dict) else []
    messages: list[ChatMessage] = []
    for index, raw in enumerate(raw_messages if isinstance(raw_messages, list) else []):
        message = _chat_message_from_raw(raw, index)
        if message is not None:
            messages.append(message)
    return messages


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


def _messages_from_checkpoint(
    snapshot: CheckpointThreadSnapshot | None,
    *,
    store: BackendDocumentStore | None = None,
    tool_call_audit: dict[str, dict[str, Any]] | None = None,
) -> list[ChatMessage]:
    if snapshot is None:
        return []

    audit_index = tool_call_audit if tool_call_audit is not None else _tool_call_audit_index(store)

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
            "result": result_text[:400] + "…" if len(result_text) > 400 else result_text,
        }
        artifact = raw.get("artifact") if isinstance(raw, dict) else getattr(raw, "artifact", None)
        curated_artifact = _curated_tool_artifact(artifact)
        if curated_artifact is not None:
            result_entry["artifact"] = curated_artifact
        if images:
            result_entry["images"] = images
        if progress_log:
            result_entry["progress_log"] = progress_log
        tool_results[str(call_id)] = result_entry

    messages: list[ChatMessage] = []
    pending_tool_calls: list[dict[str, Any]] = []
    pending_token_count = 0
    pending_reasoning_token_count = 0
    pending_cache_read_token_count = 0
    pending_reasoning_parts: list[str] = []
    pending_reasoning_timestamp: str | None = None

    for index, raw in enumerate(snapshot.messages):
        role = _raw_message_role(raw)
        if role == "tool":
            continue

        if role == "user":
            if pending_tool_calls or pending_reasoning_parts:
                synthetic = _synthetic_pending_tool_call_message(pending_tool_calls, index)
                synthetic.reasoning_text = "\n\n".join(pending_reasoning_parts)
                synthetic.reasoning_timestamp = pending_reasoning_timestamp
                synthetic.token_count = pending_token_count
                synthetic.reasoning_token_count = pending_reasoning_token_count
                synthetic.cache_read_token_count = pending_cache_read_token_count
                messages.append(synthetic)
                pending_tool_calls = []
            pending_reasoning_parts = []
            pending_token_count = 0
            pending_reasoning_token_count = 0
            pending_cache_read_token_count = 0
            pending_reasoning_timestamp = None

        if role == "assistant":
            pending_token_count += _raw_message_token_count(raw)
            pending_reasoning_token_count += extract_reasoning_token_count(raw) or 0
            pending_cache_read_token_count = (
                extract_cache_read_token_count(raw) or pending_cache_read_token_count
            )
            reasoning_text = extract_reasoning_trace(raw)
            if reasoning_text:
                pending_reasoning_parts.append(reasoning_text)
                if pending_reasoning_timestamp is None:
                    pending_reasoning_timestamp = _raw_message_created_at(raw)
            for tc in _extract_raw_tool_calls(raw):
                entry = tool_results.get(tc["id"], {})
                tc_with_result = {**tc, "result": entry.get("result", "")}
                if "artifact" in entry:
                    tc_with_result["artifact"] = entry["artifact"]
                if "images" in entry:
                    tc_with_result["images"] = entry["images"]
                if "progress_log" in entry:
                    tc_with_result["progress_log"] = entry["progress_log"]
                audit_entry = audit_index.get(tc["id"])
                if audit_entry is not None and "duration_ms" in audit_entry:
                    tc_with_result["duration_ms"] = audit_entry.get("duration_ms")
                pending_tool_calls.append(tc_with_result)

        tool_calls_to_attach = list(pending_tool_calls) if role == "assistant" else None
        message = _chat_message_from_raw(raw, index, tool_calls=tool_calls_to_attach)
        if message is not None:
            if role == "assistant":
                message.reasoning_text = "\n\n".join(pending_reasoning_parts)
                message.token_count = pending_token_count
                message.reasoning_token_count = pending_reasoning_token_count
                message.cache_read_token_count = pending_cache_read_token_count
                message.reasoning_timestamp = pending_reasoning_timestamp
                pending_reasoning_parts = []
                pending_token_count = 0
                pending_reasoning_token_count = 0
                pending_cache_read_token_count = 0
                pending_reasoning_timestamp = None
            if role in ("assistant", "user"):
                pending_tool_calls = []
            messages.append(message)

    if pending_tool_calls:
        synthetic = _synthetic_pending_tool_call_message(pending_tool_calls, len(snapshot.messages))
        synthetic.token_count = pending_token_count
        synthetic.reasoning_token_count = pending_reasoning_token_count
        synthetic.cache_read_token_count = pending_cache_read_token_count
        synthetic.reasoning_text = "\n\n".join(pending_reasoning_parts)
        synthetic.reasoning_timestamp = pending_reasoning_timestamp
        messages.append(synthetic)

    return messages


def _summary_from_session(
    payload: dict[str, Any],
    *,
    messages: list[ChatMessage],
    history_unavailable: bool = False,
) -> ChatSessionSummary:
    preview = next((message.content for message in reversed(messages) if message.content), "")
    return ChatSessionSummary(
        thread_id=payload["thread_id"],
        title=display_title(payload["title"], payload["first_message"]),
        pinned=payload["pinned"],
        archived=payload["archived"],
        created_at=payload["created_at"],
        last_activity=payload["last_activity"],
        message_count=len(messages),
        preview=preview,
        history_unavailable=history_unavailable,
    )


def _session_payload(store: BackendDocumentStore, thread_id: str) -> dict[str, Any] | None:
    document = store.get_document(PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}")
    return None if document is None else dict(document.payload)


def list_chat_sessions(
    store: BackendDocumentStore,
    *,
    include_archived: bool = False,
) -> list[ChatSessionSummary]:
    sessions: list[ChatSessionSummary] = []
    tool_call_audit = _tool_call_audit_index(store)
    checkpoint_snapshots = store.list_checkpoint_thread_snapshots(limit=100)
    snapshots_by_thread_id = {snapshot.thread_id: snapshot for snapshot in checkpoint_snapshots}

    for _key, document in store.list_documents(PROGENITOR_STATE_PREFIX, SESSION_KEY_PREFIX):
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
        messages = _messages_from_checkpoint(snapshot, tool_call_audit=tool_call_audit)
        sessions.append(
            _summary_from_session(
                payload,
                messages=messages,
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
    tool_call_audit = _tool_call_audit_index(store)
    snapshot = store.get_checkpoint_thread_snapshot(thread_id)
    messages = _messages_from_checkpoint(snapshot, tool_call_audit=tool_call_audit)
    summary = _summary_from_session(payload, messages=messages)
    return ChatSessionDetail(**summary.model_dump(), messages=messages)


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
    messages = _messages_from_checkpoint(snapshot, tool_call_audit=_tool_call_audit_index(store))
    return _summary_from_session(
        payload, messages=messages, history_unavailable=history_unavailable
    )


def patch_session_document(
    store: BackendDocumentStore,
    thread_id: str,
    request: ChatPatchRequest,
) -> dict[str, Any] | None:
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
        return payload
    if not store.merge_document(
        PROGENITOR_STATE_PREFIX, f"{SESSION_KEY_PREFIX}{thread_id}", updates
    ):
        return None
    return {**payload, **updates}


def delete_session_document(store: BackendDocumentStore, thread_id: str) -> bool:
    key = f"{SESSION_KEY_PREFIX}{thread_id}"
    existing = store.get_document(PROGENITOR_STATE_PREFIX, key)
    if existing is None:
        return False
    store.delete_document(PROGENITOR_STATE_PREFIX, key)
    return True
