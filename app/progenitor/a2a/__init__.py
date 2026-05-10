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
    dispatch_tracked_a2a_task,
    refresh_tracked_a2a_task,
    send_completion_notification,
    upsert_tracked_task_from_payload,
)

__all__ = [
    "A2AClient",
    "DEFAULT_A2A_HISTORY_LENGTH",
    "DEFAULT_A2A_POLL_SECONDS",
    "build_completion_message",
    "build_task_response",
    "dispatch_tracked_a2a_task",
    "extract_message_text",
    "is_task_payload",
    "normalize_a2a_task_status",
    "refresh_tracked_a2a_task",
    "send_completion_notification",
    "summarize_task_payload",
    "upsert_tracked_task_from_payload",
]
