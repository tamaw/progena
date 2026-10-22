"""A2A task update nodes."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime

from src.progenitor.a2a.payloads import build_completion_message
from src.progenitor.a2a.tasks import enqueue_completion_followup, refresh_tracked_a2a_task
from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.sessions.models import ConversationMessage
from src.progenitor.utils import utcnow, utcnow_isoformat

logger = logging.getLogger(__name__)


async def completion_record_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Record a completed A2A task in conversation state."""
    record = state.get("a2a_completed_task")
    if record is None:
        return {}

    metadata = {
        "type": "a2a_task_completion",
        "task_id": record.task_id,
        "agent": record.agent_name,
        "a2a_status": record.status,
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
            and existing_metadata.get("a2a_status") == record.status
        ):
            return {}

    message = ConversationMessage(
        id=f"a2a_{record.task_id}",
        role="tool",
        content=build_completion_message(record),
        timestamp=utcnow(),
        metadata=metadata,
    )
    await runtime.context.state_manager.log_conversation(message)
    messages.append(message)
    thread_id = state.get("thread_id")
    if thread_id and runtime.context.agent_graph is not None:
        try:
            await runtime.context.agent_graph.aupdate_state(
                config={"configurable": {"thread_id": str(thread_id)}},
                values={
                    "messages": [
                        {
                            "type": "tool",
                            "content": message.content,
                            "tool_call_id": message.id,
                            "id": message.id,
                        }
                    ]
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to persist a2a completion message to checkpoint for thread %s: %s",
                thread_id,
                exc,
            )
    return {"messages": messages}


async def apply_completed_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist completed A2A task records into conversation history."""
    records = _completed_records(state)
    if not records:
        return {"a2a_completed_task": None, "a2a_completed_tasks": []}

    updates: dict[str, Any] = {}
    working_state = dict(state)
    for record in records:
        working_state["a2a_completed_task"] = record
        record_updates = await completion_record_node(
            working_state,  # type: ignore[arg-type]
            runtime,
        )
        working_state.update(record_updates)
        updates.update(record_updates)
    updates["a2a_completed_task"] = None
    return updates


async def notify_completed_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Notify users about completed A2A tasks and mark them as notified."""
    records = _completed_records(state)
    if not records:
        return {"a2a_completed_tasks": []}

    notified = 0
    for record in records:
        sent = await enqueue_completion_followup(
            runtime.context.state_manager,
            runtime.context.queues,
            record,
        )
        if sent:
            notified += 1
            continue
        await runtime.context.state_manager.update_a2a_task(
            record.task_id,
            {"notification_sent_at": utcnow(), "updated_at": utcnow()},
        )
        notified += 1

    return {"a2a_completed_tasks": [], "a2a_notifications_sent": notified}


async def poll_tracked_tasks_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh tracked A2A tasks when the heartbeat runtime marks them due."""
    queued_completed = runtime.context.queues.drain_a2a_completions()
    if "a2a_poll" not in runtime.context.due_maintenance:
        return {"a2a_completed_tasks": queued_completed} if queued_completed else {}
    completed = await _poll_tracked_tasks(runtime)
    return {
        "last_a2a_poll_at": utcnow_isoformat(),
        "a2a_poll_ran": True,
        "a2a_completed_tasks": [*queued_completed, *completed],
    }


def _completed_records(state: ProgenitorState) -> list[Any]:
    records = state.get("a2a_completed_tasks")
    if isinstance(records, list) and records:
        return records
    record = state.get("a2a_completed_task")
    return [record] if record is not None else []


async def _poll_tracked_tasks(runtime: Runtime[HeartbeatContext]) -> list[Any]:
    completed: list[Any] = []
    tasks = await runtime.context.state_manager.get_active_a2a_tasks()
    for record in tasks:
        current = record
        if not record.is_terminal:
            current = await refresh_tracked_a2a_task(
                runtime.context.state_manager,
                record.task_id,
                tool_registry=runtime.context.tool_registry,
            )
        if (
            current.is_terminal
            and current.notify_on_completion
            and current.notification_sent_at is None
        ):
            completed.append(current)
    return completed
