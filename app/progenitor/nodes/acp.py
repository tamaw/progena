"""ACP run completion nodes.

Peer of ``nodes/a2a.py``. ACP runs finish in-process (a background task updates
the persisted record), so there is no remote poll: ``collect_completed_acp_tasks_node``
simply scans tracked records for terminal-but-unnotified runs each idle pass, then
``apply``/``notify`` inject a conversation message and notify the user.
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from ..acp import build_acp_completion_message, send_acp_completion_notification
from ..state.models import ConversationMessage, ProgenitorState
from ..utils import utcnow
from .context import HeartbeatContext


async def collect_completed_acp_tasks_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Collect terminal ACP runs awaiting notification (cheap, no remote calls)."""
    completed: list[Any] = []
    tasks = await runtime.context.state_manager.get_acp_tasks()
    for record in tasks:
        if (
            record.is_terminal
            and record.notify_on_completion
            and record.notification_sent_at is None
        ):
            completed.append(record)
    return {"acp_completed_tasks": completed}


async def _completion_record_node(
    record: Any,
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Record one completed ACP run in conversation state (deduped)."""
    metadata = {
        "type": "acp_task_completion",
        "task_id": record.task_id,
        "agent": record.agent_name,
        "acp_status": record.status,
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
            and existing_metadata.get("acp_status") == record.status
        ):
            return {}

    message = ConversationMessage(
        id=f"acp_{record.task_id}",
        role="tool",
        content=build_acp_completion_message(record),
        timestamp=utcnow(),
        metadata=metadata,
    )
    await runtime.context.state_manager.log_conversation(message)
    messages.append(message)
    await runtime.context.state_manager.save_messages(messages)
    return {"messages": messages}


async def apply_completed_acp_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist completed ACP run records into conversation history."""
    records = state.get("acp_completed_tasks") or []
    if not records:
        return {"acp_completed_task": None, "acp_completed_tasks": []}

    updates: dict[str, Any] = {}
    working_state = dict(state)
    for record in records:
        record_updates = await _completion_record_node(
            record,
            working_state,  # type: ignore[arg-type]
            runtime,
        )
        working_state.update(record_updates)
        updates.update(record_updates)
    updates["acp_completed_task"] = None
    return updates


async def notify_completed_acp_tasks_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Notify users about completed ACP runs and mark them notified."""
    records = state.get("acp_completed_tasks") or []
    if not records:
        return {"acp_completed_tasks": []}

    notified = 0
    for record in records:
        if runtime.context.llm_client is not None:
            sent = await send_acp_completion_notification(
                runtime.context.state_manager,
                runtime.context.llm_client,
                record,
            )
            if sent:
                notified += 1
            continue
        await runtime.context.state_manager.update_acp_task(
            record.task_id,
            {"notification_sent_at": utcnow(), "updated_at": utcnow()},
        )
        notified += 1

    return {"acp_completed_tasks": [], "acp_notifications_sent": notified}
