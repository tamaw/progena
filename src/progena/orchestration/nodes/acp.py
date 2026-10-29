"""Collect actionable ACP transitions without model-driven progress polling.

The backend owns execution. This collector routes terminal, input and stall
transitions to the originating chat while workflow tasks retain their own
document-event routing.
"""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from src.progena.acp.tasks import build_acp_completion_message, enqueue_acp_completion_followup
from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.state import ProgenaState
from src.progena.sessions.models import ConversationMessage
from src.progena.utils import utcnow


def _is_workflow_owned(record: Any) -> bool:
    """Keep workflow-owned ACP task routing out of the chat follow-up path."""
    source = str(getattr(record, "source", ""))
    return source == "workflow" or source.startswith("workflow:")


def _material_event(
    record: Any, acknowledgements: dict[str, str] | None = None
) -> tuple[str, str] | None:
    """Return one unacknowledged actionable event, never ordinary progress."""
    acknowledgements = acknowledgements or {}
    turn = int(getattr(record, "turn", 0))
    if record.is_terminal:
        identity = f"terminal:{turn}:{record.status}"
        return ("terminal", identity) if acknowledgements.get("terminal") != identity else None
    pending = getattr(record, "pending_input", None)
    if isinstance(pending, dict):
        input_id = str(pending.get("id") or "").strip()
        identity = f"pending_input:{turn}:{input_id}"
        if input_id and acknowledgements.get("pending_input") != identity:
            return "pending_input", identity
    if bool(getattr(record, "stalled", False)):
        identity = f"stall:{turn}:{int(getattr(record, 'stall_episode', 0))}"
        if acknowledgements.get("stall") != identity:
            return "stall", identity
    return None


def _was_historically_notified(
    record: Any, event_type: str, acknowledgements: dict[str, str]
) -> bool:
    """Avoid replaying a terminal event acknowledged before material-event acks."""
    if event_type != "terminal" or acknowledgements or record.notification_sent_at is None:
        return False
    completed_at = record.completed_at
    return completed_at is None or completed_at <= record.notification_sent_at


async def collect_completed_acp_tasks_node(
    _state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Collect chat-owned terminal/input/stall events awaiting acknowledgement."""
    actionable: list[Any] = []
    tasks = await runtime.context.state_manager.get_acp_tasks()
    for record in tasks:
        if _is_workflow_owned(record):
            continue
        if not record.notify_on_completion:
            # Earlier acp_start versions disabled notifications. Adopt only
            # still-active chat tasks with explicit caller/thread provenance;
            # do not replay old terminal results or capture workflow tasks.
            metadata = record.request_metadata
            if record.is_terminal or not metadata.get("caller") or not metadata.get("thread_id"):
                continue
            await runtime.context.state_manager.update_acp_task(
                record.task_id, {"notify_on_completion": True}
            )
            record.notify_on_completion = True
        acknowledgements = dict(getattr(record, "material_event_acknowledgements", {}) or {})
        event = _material_event(record, acknowledgements)
        if event is None:
            continue
        event_type, identity = event
        if _was_historically_notified(record, event_type, acknowledgements):
            continue
        actionable.append(record)
    return {"acp_completed_tasks": actionable}


async def _completion_record_node(
    record: Any,
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Record one terminal ACP turn in conversation state (deduped by turn)."""
    metadata = {
        "type": "acp_task_completion",
        "task_id": record.task_id,
        "agent": record.agent_name,
        "acp_status": record.status,
        "acp_turn": record.turn,
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
            and existing_metadata.get("acp_turn") == record.turn
        ):
            return {}

    message = ConversationMessage(
        id=f"acp_{record.task_id}_{record.turn}",
        role="tool",
        content=build_acp_completion_message(record),
        timestamp=utcnow(),
        metadata=metadata,
    )
    messages.append(message)
    return {"messages": messages}


async def apply_completed_acp_tasks_node(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Persist terminal ACP records into conversation history."""
    records = [record for record in (state.get("acp_completed_tasks") or []) if record.is_terminal]
    if not records:
        return {"acp_completed_task": None}

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
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Notify users about actionable ACP events and acknowledge each identity."""
    records = state.get("acp_completed_tasks") or []
    if not records:
        return {"acp_completed_tasks": []}

    notified = 0
    for record in records:
        acknowledgements = dict(record.material_event_acknowledgements)
        event = _material_event(record, acknowledgements)
        if event is None:
            continue
        event_type, identity = event
        if await enqueue_acp_completion_followup(
            runtime.context.state_manager,
            runtime.context.queues,
            record,
            event_type=event_type,
            event_identity=identity,
        ):
            notified += 1
            continue
        # Preserve the existing best-effort queue semantics: no thread route
        # still consumes the event instead of reprocessing it every heartbeat.
        acknowledgements = dict(record.material_event_acknowledgements)
        acknowledgements[event_type] = identity
        update: dict[str, Any] = {
            "material_event_acknowledgements": acknowledgements,
            "updated_at": utcnow(),
        }
        if event_type == "terminal":
            update["notification_sent_at"] = utcnow()
        await runtime.context.state_manager.update_acp_task(record.task_id, update)
        notified += 1

    return {"acp_completed_tasks": [], "acp_notifications_sent": notified}
