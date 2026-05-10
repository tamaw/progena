"""Heartbeat nodes for tracked async-subagent work."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from ..async_subagents import build_completion_message, refresh_tracked_async_subagent_task
from ..state.models import ConversationMessage, ProgenitorState
from ..utils import utcnow, utcnow_isoformat
from .context import HeartbeatContext


async def completion_record_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Record a completed async-subagent task in conversation history."""
    record = state.get("async_subagent_completed_task")
    if record is None:
        return {}

    metadata = {
        "type": "async_subagent_completion",
        "task_id": record.task_id,
        "agent": record.agent_name,
        "async_subagent_status": record.status,
        "source": record.source,
    }
    messages = list(state.get("messages", []))
    for existing in messages:
        existing_metadata = (
            existing.get("metadata")
            if isinstance(existing, dict)
            else getattr(existing, "metadata", None)
        )
        if (
            isinstance(existing_metadata, dict)
            and existing_metadata.get("type") == metadata["type"]
            and existing_metadata.get("task_id") == record.task_id
            and existing_metadata.get("async_subagent_status") == record.status
        ):
            return {}

    message = ConversationMessage(
        id=f"async_subagent_{record.task_id}",
        role="tool",
        content=build_completion_message(record),
        timestamp=utcnow(),
        metadata=metadata,
    )
    await runtime.context.state_manager.log_conversation(message)
    messages.append(message)
    await runtime.context.state_manager.save_messages(messages)
    return {"messages": messages}


async def apply_completed_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist completed async-subagent task records into conversation history."""
    records = _completed_records(state)
    if not records:
        return {
            "async_subagent_completed_task": None,
            "async_subagent_completed_tasks": [],
        }

    updates: dict[str, Any] = {}
    working_state = dict(state)
    for record in records:
        working_state["async_subagent_completed_task"] = record
        record_updates = await completion_record_node(
            working_state,  # type: ignore[arg-type]
            runtime,
        )
        working_state.update(record_updates)
        updates.update(record_updates)
    updates["async_subagent_completed_task"] = None
    return updates


async def notify_completed_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Emit IPC callbacks for completed async-subagent tasks."""
    records = _completed_records(state)
    if not records:
        return {"async_subagent_completed_tasks": []}

    notified = 0
    for record in records:
        await runtime.context.queues.push_event(
            "agent_message",
            build_completion_message(record),
            {
                "source": "async_subagent_completion",
                "task_id": record.task_id,
                "agent": record.agent_name,
                "status": record.status,
                "thread_id": record.parent_thread_id,
            },
        )
        await runtime.context.state_manager.update_async_subagent_task(
            record.task_id,
            {"notification_sent_at": utcnow(), "updated_at": utcnow()},
        )
        notified += 1

    return {
        "async_subagent_completed_tasks": [],
        "async_subagent_notifications_sent": notified,
    }


async def poll_tracked_tasks_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh tracked async-subagent tasks when heartbeat marks them due."""
    if "async_subagent_poll" not in runtime.context.due_maintenance:
        return {}
    completed = await _poll_tracked_tasks(runtime)
    return {
        "last_async_subagent_poll_at": utcnow_isoformat(),
        "async_subagent_poll_ran": True,
        "async_subagent_completed_tasks": completed,
    }


def _completed_records(state: ProgenitorState) -> list[Any]:
    records = state.get("async_subagent_completed_tasks")
    if isinstance(records, list) and records:
        return records
    record = state.get("async_subagent_completed_task")
    return [record] if record is not None else []


async def _poll_tracked_tasks(runtime: Runtime[HeartbeatContext]) -> list[Any]:
    completed: list[Any] = []
    tasks = await runtime.context.state_manager.get_async_subagent_tasks()
    for record in tasks:
        current = record
        if not record.is_terminal:
            current = await refresh_tracked_async_subagent_task(
                runtime.context.state_manager,
                record.task_id,
            )
        if (
            current.is_terminal
            and current.notify_on_completion
            and current.notification_sent_at is None
        ):
            completed.append(current)
    return completed
