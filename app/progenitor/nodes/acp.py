"""ACP task completion heartbeat nodes.

The backend ACP supervisor drives task documents to a terminal state.  The
heartbeat only finds terminal, unnotified records and feeds their result back
into the originating conversation.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime

from ..acp import build_acp_completion_message, enqueue_acp_completion_followup
from ..state.models import ConversationMessage, ProgenitorState
from ..utils import utcnow
from .context import HeartbeatContext

logger = logging.getLogger(__name__)


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
                "Failed to persist acp completion message to checkpoint for thread %s: %s",
                thread_id,
                exc,
            )
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
        if await enqueue_acp_completion_followup(
            runtime.context.state_manager,
            runtime.context.queues,
            record,
        ):
            notified += 1
            continue
        # No llm_client, or the client can't push events: mark the record notified
        # anyway so the heartbeat doesn't reprocess it forever.
        await runtime.context.state_manager.update_acp_task(
            record.task_id,
            {"notification_sent_at": utcnow(), "updated_at": utcnow()},
        )
        notified += 1

    return {"acp_completed_tasks": [], "acp_notifications_sent": notified}
