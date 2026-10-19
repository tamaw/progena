"""A2A integration package for Progenitor."""

from .client import A2AClient
from .payloads import (
    build_completion_message,
    build_task_response,
    extract_message_text,
    is_task_payload,
    normalize_a2a_task_status,
    summarize_task_payload,
)
from .tasks import (
    DEFAULT_A2A_HISTORY_LENGTH,
    DEFAULT_A2A_POLL_SECONDS,
    cancel_tracked_a2a_task,
    dispatch_tracked_a2a_task,
    enqueue_completion_followup,
    refresh_tracked_a2a_task,
    upsert_tracked_task_from_payload,
)

__all__ = [
    "A2AClient",
    "DEFAULT_A2A_HISTORY_LENGTH",
    "DEFAULT_A2A_POLL_SECONDS",
    "build_completion_message",
    "build_task_response",
    "cancel_tracked_a2a_task",
    "dispatch_tracked_a2a_task",
    "enqueue_completion_followup",
    "extract_message_text",
    "is_task_payload",
    "normalize_a2a_task_status",
    "refresh_tracked_a2a_task",
    "summarize_task_payload",
    "upsert_tracked_task_from_payload",
]
