"""Backend chat session read model and UI metadata persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.progenitor.runtime.messages import (
    extract_message_text,
    extract_reasoning_token_count,
    extract_reasoning_trace,
)

from .document_store import BackendDocumentStore, CheckpointThreadSnapshot

CHAT_METADATA_PREFIX = "backend.chat.sessions"
CHAT_INDEX_KEY = "__index__"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ChatMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str | None = None
    token_count: int = 0
    reasoning_token_count: int = 0
    reasoning_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatSessionSummary(BaseModel):
    chat_session_id: str
    title: str
    thread_id: str | None = None
    progenitor_session_id: str | None = None
    checkpoint_id: str | None = None
    pinned: bool = False
    shared: bool = False
    archived: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    last_activity: str | None = None
    message_count: int = 0
    preview: str = ""


class ChatSessionDetail(ChatSessionSummary):
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionSummary]
    total: int


class ChatCreateRequest(BaseModel):
    title: str | None = None
    thread_id: str | None = None
    progenitor_session_id: str | None = None
    checkpoint_id: str | None = None


class ChatPatchRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    shared: bool | None = None
    archived: bool | None = None


class ChatMutationResponse(BaseModel):
    session: ChatSessionDetail


class UiEventEnvelope(BaseModel):
    id: str
    seq: int
    type: str
    scope: Literal["global", "session", "chat"] = "global"
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now_iso)


def _metadata_index(store: BackendDocumentStore) -> list[str]:
    document = store.get_document(CHAT_METADATA_PREFIX, CHAT_INDEX_KEY, [])
    if document is None or not isinstance(document.payload, list):
        return []
    return [str(item) for item in document.payload if item]


def _save_metadata_index(store: BackendDocumentStore, ids: list[str]) -> None:
    ordered = list(dict.fromkeys(ids))
    store.save_document(CHAT_METADATA_PREFIX, CHAT_INDEX_KEY, ordered)


def _get_metadata(store: BackendDocumentStore, chat_session_id: str) -> dict[str, Any] | None:
    document = store.get_document(CHAT_METADATA_PREFIX, chat_session_id)
    if document is None or not isinstance(document.payload, dict):
        return None
    return dict(document.payload)


def _save_metadata(
    store: BackendDocumentStore, chat_session_id: str, metadata: dict[str, Any]
) -> None:
    store.save_document(CHAT_METADATA_PREFIX, chat_session_id, metadata)
    ids = _metadata_index(store)
    if chat_session_id not in ids:
        ids.append(chat_session_id)
        _save_metadata_index(store, ids)


def _delete_metadata(store: BackendDocumentStore, chat_session_id: str) -> None:
    metadata = _get_metadata(store, chat_session_id) or {}
    metadata["deleted"] = True
    metadata["updated_at"] = _now_iso()
    _save_metadata(store, chat_session_id, metadata)


def create_chat_metadata(
    store: BackendDocumentStore,
    request: ChatCreateRequest,
) -> dict[str, Any]:
    now = _now_iso()
    chat_session_id = f"chat_{uuid.uuid4().hex[:12]}"
    thread_id = request.thread_id or _thread_id_from_session_id(request.progenitor_session_id)
    metadata = {
        "chat_session_id": chat_session_id,
        "title": (request.title or "New chat").strip() or "New chat",
        "thread_id": thread_id,
        "progenitor_session_id": request.progenitor_session_id,
        "checkpoint_id": request.checkpoint_id,
        "pinned": False,
        "shared": False,
        "archived": False,
        "created_at": now,
        "updated_at": now,
        "last_activity": now,
    }
    _save_metadata(store, chat_session_id, metadata)
    return metadata


def materialize_chat_metadata(
    store: BackendDocumentStore,
    session: ChatSessionSummary | ChatSessionDetail,
) -> dict[str, Any]:
    """Persist backend UI metadata for a checkpoint-derived chat summary."""
    metadata = {
        "chat_session_id": session.chat_session_id,
        "title": session.title,
        "thread_id": session.thread_id,
        "progenitor_session_id": session.progenitor_session_id,
        "checkpoint_id": session.checkpoint_id,
        "pinned": session.pinned,
        "shared": session.shared,
        "archived": session.archived,
        "created_at": session.created_at or _now_iso(),
        "updated_at": session.updated_at or _now_iso(),
        "last_activity": session.last_activity or session.updated_at or _now_iso(),
    }
    _save_metadata(store, session.chat_session_id, metadata)
    return metadata


def patch_chat_metadata(
    store: BackendDocumentStore,
    chat_session_id: str,
    request: ChatPatchRequest,
) -> dict[str, Any] | None:
    metadata = _get_metadata(store, chat_session_id)
    if metadata is None or metadata.get("deleted") is True:
        return None
    if request.title is not None:
        title = request.title.strip()
        if title:
            metadata["title"] = title
    if request.pinned is not None:
        metadata["pinned"] = request.pinned
    if request.shared is not None:
        metadata["shared"] = request.shared
    if request.archived is not None:
        metadata["archived"] = request.archived
    metadata["updated_at"] = _now_iso()
    _save_metadata(store, chat_session_id, metadata)
    return metadata


def delete_chat_metadata(store: BackendDocumentStore, chat_session_id: str) -> bool:
    if _get_metadata(store, chat_session_id) is None:
        return False
    _delete_metadata(store, chat_session_id)
    return True


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


def _thread_id_from_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    if session_id.startswith("thread_"):
        return session_id
    return f"thread_{session_id}"


def _session_id_from_thread_id(thread_id: str | None) -> str | None:
    if not thread_id:
        return None
    return thread_id.removeprefix("thread_")


def _thread_id_for_metadata(metadata: dict[str, Any]) -> str | None:
    thread_id = metadata.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    progenitor_session_id = metadata.get("progenitor_session_id")
    if isinstance(progenitor_session_id, str) and progenitor_session_id:
        return _thread_id_from_session_id(progenitor_session_id)
    chat_session_id = metadata.get("chat_session_id")
    if isinstance(chat_session_id, str) and chat_session_id.startswith(("sess_", "thread_")):
        return _thread_id_from_session_id(chat_session_id)
    return None


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
    else:
        created_at = (
            getattr(raw, "created_at", None)
            or getattr(raw, "timestamp", None)
            or getattr(raw, "time", None)
        )
        metadata = getattr(raw, "response_metadata", None)
    if created_at is None and isinstance(metadata, dict):
        created_at = metadata.get("created_at")
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


def _extract_raw_tool_calls(raw: Any) -> list[dict[str, Any]]:
    """Return tool_calls list from an AIMessage (normalized LangChain format)."""
    if isinstance(raw, dict):
        tool_calls = raw.get("tool_calls")
    else:
        tool_calls = getattr(raw, "tool_calls", None)
    if not isinstance(tool_calls, list):
        return []
    result = []
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
            }
        )
    return result


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
        content = _message_text(raw.get("content") or raw.get("message") or raw.get("text"))
    else:
        content = extract_message_text(raw) or _message_text(getattr(raw, "content", ""))
    if not content and role == "assistant":
        return None
    return ChatMessage(
        id=_raw_message_id(raw, index),
        role=role,
        content=content,
        created_at=_raw_message_created_at(raw),
        token_count=_raw_message_token_count(raw),
        reasoning_token_count=extract_reasoning_token_count(raw) or 0,
        reasoning_text=extract_reasoning_trace(raw) or "",
        tool_calls=tool_calls or [],
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


def _messages_from_checkpoint(snapshot: CheckpointThreadSnapshot | None) -> list[ChatMessage]:
    if snapshot is None:
        return []

    # First pass: index ToolMessage results by tool_call_id.
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
        result_text = _message_text(raw_content)
        result_entry: dict[str, Any] = {
            "result": result_text[:400] + "…" if len(result_text) > 400 else result_text,
        }
        if images:
            result_entry["images"] = images
        tool_results[str(call_id)] = result_entry

    # Second pass: build messages, accumulating tool calls from tool-only AIMessages
    # and attaching them to the next AIMessage that produces visible text.
    messages: list[ChatMessage] = []
    pending_tool_calls: list[dict[str, Any]] = []

    for index, raw in enumerate(snapshot.messages):
        role = _raw_message_role(raw)
        if role == "tool":
            continue

        if role == "assistant":
            for tc in _extract_raw_tool_calls(raw):
                entry = tool_results.get(tc["id"], {})
                tc_with_result = {**tc, "result": entry.get("result", "")}
                if "images" in entry:
                    tc_with_result["images"] = entry["images"]
                pending_tool_calls.append(tc_with_result)

        tool_calls_to_attach = list(pending_tool_calls) if role == "assistant" else None
        message = _chat_message_from_raw(raw, index, tool_calls=tool_calls_to_attach)
        if message is not None:
            if role in ("assistant", "user"):
                pending_tool_calls = []
            messages.append(message)

    return messages


def _summary_from_metadata(
    metadata: dict[str, Any],
    *,
    fallback_messages: list[ChatMessage] | None = None,
) -> ChatSessionSummary:
    messages = fallback_messages or []
    preview = next((message.content for message in reversed(messages) if message.content), "")
    raw_title = str(metadata.get("title") or "Untitled chat")
    title = _display_title(raw_title, messages)
    return ChatSessionSummary(
        chat_session_id=str(metadata["chat_session_id"]),
        title=title,
        thread_id=metadata.get("thread_id"),
        progenitor_session_id=metadata.get("progenitor_session_id"),
        checkpoint_id=metadata.get("checkpoint_id"),
        pinned=bool(metadata.get("pinned", False)),
        shared=bool(metadata.get("shared", False)),
        archived=bool(metadata.get("archived", False)),
        created_at=metadata.get("created_at"),
        updated_at=metadata.get("updated_at"),
        last_activity=metadata.get("last_activity") or metadata.get("updated_at"),
        message_count=len(messages),
        preview=preview,
    )


def _metadata_without_messages(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if key != "messages"}


def _display_title(raw_title: str, messages: list[ChatMessage]) -> str:
    title = raw_title.strip() or "Untitled chat"
    if title.lower() not in {"new chat", "new conversation", "untitled chat"}:
        return title
    first_user_message = next(
        (message.content for message in messages if message.role == "user"), ""
    )
    if not first_user_message:
        return title
    normalized = " ".join(first_user_message.split())
    return normalized[:64] if normalized else title


def _checkpoint_snapshot_for_metadata(
    store: BackendDocumentStore,
    metadata: dict[str, Any],
) -> CheckpointThreadSnapshot | None:
    thread_id = _thread_id_for_metadata(metadata)
    if thread_id is None:
        return None
    checkpoint_id = metadata.get("checkpoint_id")
    return store.get_checkpoint_thread_snapshot(
        thread_id,
        checkpoint_id=str(checkpoint_id) if checkpoint_id else None,
    )


def _metadata_with_checkpoint(
    metadata: dict[str, Any],
    snapshot: CheckpointThreadSnapshot | None,
) -> dict[str, Any]:
    if snapshot is None:
        return metadata
    progenitor_session_id = metadata.get("progenitor_session_id") or _session_id_from_thread_id(
        snapshot.thread_id
    )
    return {
        **metadata,
        "thread_id": metadata.get("thread_id") or snapshot.thread_id,
        "progenitor_session_id": progenitor_session_id,
        "checkpoint_id": snapshot.checkpoint_id or metadata.get("checkpoint_id"),
        "updated_at": snapshot.checkpoint_ts or metadata.get("updated_at"),
        "last_activity": snapshot.checkpoint_ts
        or metadata.get("last_activity")
        or metadata.get("updated_at"),
    }


def _checkpoint_metadata(snapshot: CheckpointThreadSnapshot) -> dict[str, Any]:
    session_id = _session_id_from_thread_id(snapshot.thread_id)
    return {
        "chat_session_id": session_id or snapshot.thread_id,
        "title": "New chat",
        "thread_id": snapshot.thread_id,
        "progenitor_session_id": session_id,
        "checkpoint_id": snapshot.checkpoint_id,
        "created_at": snapshot.checkpoint_ts,
        "updated_at": snapshot.checkpoint_ts,
        "last_activity": snapshot.checkpoint_ts,
        "pinned": False,
        "shared": False,
        "archived": False,
    }


def list_chat_sessions(
    store: BackendDocumentStore,
    *,
    include_archived: bool = False,
) -> list[ChatSessionSummary]:
    sessions: list[ChatSessionSummary] = []
    checkpoint_snapshots = store.list_checkpoint_thread_snapshots(limit=100)
    covered_threads: set[str] = set()
    hidden_threads: set[str] = set()
    for chat_session_id in _metadata_index(store):
        metadata = _get_metadata(store, chat_session_id)
        if metadata is None:
            continue
        metadata_thread_id = _thread_id_for_metadata(metadata)
        if metadata.get("deleted") is True:
            if metadata_thread_id:
                hidden_threads.add(metadata_thread_id)
            continue
        if metadata.get("archived") is True and not include_archived:
            continue
        snapshot = _checkpoint_snapshot_for_metadata(store, metadata)
        fallback_messages = _messages_from_checkpoint(snapshot)
        if snapshot is not None:
            covered_threads.add(snapshot.thread_id)
            metadata = _metadata_with_checkpoint(metadata, snapshot)
        sessions.append(_summary_from_metadata(metadata, fallback_messages=fallback_messages))

    for snapshot in checkpoint_snapshots:
        if snapshot.thread_id in covered_threads or snapshot.thread_id in hidden_threads:
            continue
        messages = _messages_from_checkpoint(snapshot)
        if not messages:
            continue
        summary = _summary_from_metadata(_checkpoint_metadata(snapshot), fallback_messages=messages)
        if include_archived or not summary.archived:
            sessions.append(summary)

    sessions.sort(
        key=lambda item: (item.pinned, item.last_activity or item.updated_at or ""), reverse=True
    )
    return sessions


def get_chat_detail(
    store: BackendDocumentStore,
    chat_session_id: str,
) -> ChatSessionDetail | None:
    metadata = _get_metadata(store, chat_session_id)
    messages: list[ChatMessage] = []
    snapshot: CheckpointThreadSnapshot | None = None
    if metadata is not None:
        snapshot = _checkpoint_snapshot_for_metadata(store, metadata)
        messages = _messages_from_checkpoint(snapshot)
        metadata = _metadata_with_checkpoint(metadata, snapshot)
    else:
        thread_id = _thread_id_from_session_id(chat_session_id)
        snapshot = store.get_checkpoint_thread_snapshot(thread_id) if thread_id else None
        if snapshot is not None:
            messages = _messages_from_checkpoint(snapshot)
            metadata = _checkpoint_metadata(snapshot)
    if metadata is None or metadata.get("deleted") is True:
        return None
    summary = _summary_from_metadata(metadata, fallback_messages=messages)
    return ChatSessionDetail(**summary.model_dump(), messages=messages)


def current_state_chat_detail(state: dict[str, Any]) -> ChatSessionDetail | None:
    """Build a chat detail for the live "current state" session (current_state.json).

    Used to materialize backend UI metadata for the not-yet-persisted current
    session when the user pins/archives/deletes its card.
    """
    if not isinstance(state, dict):
        return None
    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    messages = _messages_from_state(state)
    now = _now_iso()
    metadata = {
        "chat_session_id": session_id,
        "title": str(state.get("title") or "Current chat"),
        "thread_id": _thread_id_from_session_id(session_id),
        "progenitor_session_id": session_id,
        "checkpoint_id": None,
        "pinned": False,
        "shared": False,
        "archived": False,
        "created_at": now,
        "updated_at": now,
        "last_activity": now,
    }
    summary = _summary_from_metadata(metadata, fallback_messages=messages)
    return ChatSessionDetail(**summary.model_dump(), messages=messages)
