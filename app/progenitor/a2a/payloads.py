"""Helpers for A2A task payload normalization and presentation."""

from __future__ import annotations

from typing import Any

from ..state.models import A2ATaskRecord, A2ATaskStatus


def normalize_a2a_task_status(raw_state: Any) -> A2ATaskStatus:
    """Map raw SDK task state values onto the persisted status enum."""
    value = str(raw_state or "").strip().lower()
    if value == "input_required":
        value = A2ATaskStatus.INPUT_REQUIRED.value
    if value == "cancelled":
        value = A2ATaskStatus.CANCELED.value
    try:
        return A2ATaskStatus(value)
    except ValueError:
        return A2ATaskStatus.UNKNOWN


def _extract_text_from_parts(parts: Any) -> str | None:
    if not isinstance(parts, list):
        return None

    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            text_parts.append(text.strip())
            continue
        root = part.get("root")
        if isinstance(root, dict):
            root_text = root.get("text")
            if isinstance(root_text, str) and root_text.strip():
                text_parts.append(root_text.strip())

    if not text_parts:
        return None
    return "\n".join(text_parts)


def extract_message_text(message_payload: Any) -> str | None:
    """Extract the most useful human-readable text from an A2A message payload."""
    if isinstance(message_payload, str) and message_payload.strip():
        return message_payload.strip()
    if not isinstance(message_payload, dict):
        return None
    parts = message_payload.get("parts")
    text = _extract_text_from_parts(parts)
    if text:
        return text
    for key in ("text", "content"):
        value = message_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# A long-running real agent can emit one status update every couple of
# seconds for many minutes -- without a bound, that log is re-embedded into
# every subsequent ToolMessage the model sees on this thread (build_task_
# response), re-persisted on every refresh, and re-parsed on every session
# load (chat_sessions.py), growing without limit for the life of the task.
# Keeping the most recent entries preserves what a viewer actually cares
# about (what's happening now) over what's least likely to still matter
# (the earliest updates from a long-since-passed phase).
_MAX_PROGRESS_LOG_ENTRIES = 50
_MAX_PROGRESS_MESSAGE_TEXT_LENGTH = 500


def extract_progress_log(progress_updates: Any, agent_name: str = "") -> list[dict[str, str]]:
    """Compact A2AClient.call_agent()'s raw `progress_updates` (full
    TaskStatusUpdateEvent payloads) down to the {state, message_text,
    agent_name, timestamp} entries a reloaded chat needs to rebuild the same
    progress chain a live viewer saw -- callers persist this instead of the
    raw envelopes (task/context ids, kind markers, ...), which add nothing
    for that purpose and would only get truncated away anyway (see
    chat_sessions.py's 400-char result preview). Bounded to the most recent
    `_MAX_PROGRESS_LOG_ENTRIES`, each with `message_text` capped to
    `_MAX_PROGRESS_MESSAGE_TEXT_LENGTH`, so a long-running task's log can't
    grow the persisted ToolMessage (and every future turn's context) without
    limit.

    `agent_name` isn't part of the raw TaskStatusUpdateEvent -- it's the
    caller's own resolved agent name (see dispatch_tracked_a2a_task), stamped
    onto every entry so a reload can repopulate the card header the same way
    the live `a2a_call_progress` stream event does (see
    build_a2a_call_progress_event); without it, a reload has no source for
    the name at all once the task auto-routed (the LLM's raw tool-call args
    never named an agent) and falls back to a generic placeholder.

    `timestamp` comes from `_progenitor_received_at`, stamped by
    A2AClient.call_agent() at the moment each update actually arrived (see
    client.py) -- extraction itself only runs once, after the whole blocking
    call has completed, so there's no other way to recover each entry's real
    time instead of collapsing them all onto one post-hoc "now"."""
    if not isinstance(progress_updates, list):
        return []
    entries: list[dict[str, str]] = []
    for update in progress_updates:
        if not isinstance(update, dict):
            continue
        status = update.get("status")
        state = str(status.get("state") or "") if isinstance(status, dict) else ""
        message_text = (
            extract_message_text(status.get("message")) if isinstance(status, dict) else ""
        ) or ""
        if len(message_text) > _MAX_PROGRESS_MESSAGE_TEXT_LENGTH:
            message_text = message_text[:_MAX_PROGRESS_MESSAGE_TEXT_LENGTH] + "…"
        timestamp = str(update.get("_progenitor_received_at") or "")
        if state or message_text:
            entries.append(
                {
                    "state": state,
                    "message_text": message_text,
                    "agent_name": agent_name,
                    "timestamp": timestamp,
                }
            )
    if len(entries) > _MAX_PROGRESS_LOG_ENTRIES:
        entries = entries[-_MAX_PROGRESS_LOG_ENTRIES:]
    return entries


def summarize_task_payload(task_payload: dict[str, Any]) -> str | None:
    """Produce a compact user-facing progress summary from an A2A task snapshot."""
    status = task_payload.get("status")
    if isinstance(status, dict):
        status_text = extract_message_text(status.get("message"))
        if status_text:
            return status_text

    history = task_payload.get("history")
    if isinstance(history, list):
        for item in reversed(history):
            text = extract_message_text(item)
            if text:
                return text

    artifacts = task_payload.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in reversed(artifacts):
            if not isinstance(artifact, dict):
                continue
            text = extract_message_text(artifact)
            if text:
                return text
            parts_text = _extract_text_from_parts(artifact.get("parts"))
            if parts_text:
                return parts_text

    return None


def is_task_payload(payload: Any) -> bool:
    """Return whether *payload* looks like a serialized A2A Task."""
    return (
        isinstance(payload, dict)
        and str(payload.get("kind") or "task") == "task"
        and isinstance(payload.get("id"), str)
    )


def build_task_response(record: A2ATaskRecord) -> dict[str, Any]:
    """Format a tracked task record for tool responses."""
    return {
        "task_id": record.task_id,
        "agent": record.agent_name,
        "status": record.status,
        "context_id": record.context_id,
        "summary": record.summary,
        "latest_message": record.latest_message,
        "last_error": record.last_error,
        "notify_on_completion": record.notify_on_completion,
        "notification_sent_at": record.notification_sent_at.isoformat()
        if record.notification_sent_at
        else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "task": record.latest_task,
        "progress_log": record.progress_log,
    }


def _build_notification_title(record: A2ATaskRecord) -> str:
    agent_name = record.agent_name.replace("_", " ").strip() or "Agent"
    status = record.status.replace("-", " ")
    return f"{agent_name} task {status}"


def _build_notification_body(record: A2ATaskRecord) -> str:
    summary = (record.summary or record.latest_message or "").strip()
    if summary:
        return f"{summary}\n\ntask_id: {record.task_id}"
    return f"{record.agent_name} task {record.task_id} is {record.status}."


def notification_level(record: A2ATaskRecord) -> str:
    """Return the UI notification severity for a tracked task."""
    if record.status == A2ATaskStatus.COMPLETED.value:
        return "success"
    if record.status in {A2ATaskStatus.FAILED.value, A2ATaskStatus.REJECTED.value}:
        return "error"
    if record.status in {A2ATaskStatus.CANCELED.value, A2ATaskStatus.AUTH_REQUIRED.value}:
        return "warning"
    return "info"


def build_completion_message(record: A2ATaskRecord) -> str:
    """Build a user- and agent-readable completion update for a tracked task."""
    title = _build_notification_title(record).strip()
    body = _build_notification_body(record).strip()
    if not title:
        return body
    if not body:
        return title
    return f"{title}\n\n{body}"
