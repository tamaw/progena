"""Input normalization nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.runtime import Runtime

from ..schedule import SCHEDULED_INPUT_PREFIX
from ..state.models import BackgroundTaskEntry, ProgenitorState
from .context import HeartbeatContext


def extract_input_payload(queued_input: Any) -> tuple[str, dict[str, Any]]:
    """Normalize queued input into plain content plus optional metadata."""

    if isinstance(queued_input, dict):
        content = str(queued_input.get("content") or "").strip()
        metadata = dict(queued_input.get("metadata") or {})
        return content, metadata
    return str(queued_input).strip(), {}


async def dequeue_input_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Dequeue one pending input item."""
    if state.get("queued_input") is not None:
        return {}
    queued_input = await runtime.context.queues.get_input(runtime.context.input_timeout)
    if queued_input is not None:
        return {"queued_input": queued_input}

    dequeue_background = getattr(runtime.context.state_manager, "dequeue_background_task", None)
    if not callable(dequeue_background):
        return {"queued_input": None}

    dequeue_background_fn = cast(
        Callable[[], Awaitable[BackgroundTaskEntry | None]],
        dequeue_background,
    )
    background_task = await dequeue_background_fn()
    if background_task is None:
        return {"queued_input": None}
    return {
        "queued_input": {
            "content": background_task.content,
            "metadata": background_task.metadata,
        }
    }


async def cycle_setup_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Prepare state for one input-driven cycle."""
    user_input, input_metadata = extract_input_payload(state.get("queued_input"))
    if not user_input:
        return {
            "queued_input": None,
            "current_input": None,
            "current_input_role": None,
            "current_input_metadata": None,
            "requested_thread_id": None,
            "is_isolated_thread": False,
            "is_scheduled_input": False,
            "scheduled_message": None,
        }

    input_role = str((input_metadata or {}).get("role") or "").strip() or None
    if input_role:
        input_metadata = {
            key: value for key, value in (input_metadata or {}).items() if key != "role"
        }

    requested_thread_id = str((input_metadata or {}).get("thread_id") or "").strip() or None
    current_thread_id = str(state.get("thread_id") or "").strip()
    is_scheduled_input = user_input.startswith(SCHEDULED_INPUT_PREFIX)
    scheduled_message = (
        user_input[len(SCHEDULED_INPUT_PREFIX) :].strip() if is_scheduled_input else None
    )
    input_wait_seconds = runtime.context.queues.current_input_wait_seconds
    pending_messages = runtime.context.queues.current_input_pending_count

    return {
        "queued_input": None,
        "current_input": user_input,
        "current_input_role": input_role,
        "current_input_metadata": input_metadata or None,
        "requested_thread_id": requested_thread_id,
        "is_isolated_thread": bool(
            requested_thread_id and requested_thread_id != current_thread_id
        ),
        "is_scheduled_input": is_scheduled_input,
        "scheduled_message": scheduled_message,
        "cycle_count": int(state.get("cycle_count", 0) or 0) + 1,
        "actions_this_cycle": 0,
        "input_wait_seconds": float(input_wait_seconds),
        "pending_messages_in_queue": int(pending_messages),
        "idle_cycles": 0,
        "current_backoff_seconds": float(runtime.context.idle_backoff_seconds),
        "heartbeat_action": "processed",
        "cycle_error": None,
        "cycle_complete_signalled": False,
    }


async def normalize_input_node(state: ProgenitorState) -> dict[str, Any]:
    """Normalize the current input fields."""
    current_input = str(state.get("current_input") or "").strip()
    return {
        "current_input": current_input or None,
        "current_input_role": state.get("current_input_role"),
        "current_input_metadata": state.get("current_input_metadata"),
    }
