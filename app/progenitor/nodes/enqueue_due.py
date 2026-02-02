"""
EnqueueDue Node - Converts due schedule items into backlog work items.
"""

from datetime import datetime
from typing import Callable, Coroutine, Any
import uuid

from ..state.models import ProgenitorState, WorkItem, WorkItemType, WorkItemStatus
from ..runtime.time_utils import utcnow_isoformat


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the enqueue due node function."""

    async def enqueue_due_node(state: ProgenitorState) -> ProgenitorState:
        """Enqueue due scheduled items as work items."""
        due_ids = state.get("due_items", [])

        if not due_ids:
            return {}

        schedule = state.get("schedule", [])
        backlog = list(state.get("backlog", []))

        # Find schedule entries and create work items
        for schedule_entry in schedule:
            entry_dict = schedule_entry if isinstance(schedule_entry, dict) else schedule_entry.model_dump()

            if entry_dict.get("id") not in due_ids:
                continue

            # Check if already in backlog (idempotent)
            existing = any(
                (isinstance(item, dict) and item.get("context", {}).get("schedule_id") == entry_dict.get("id")) or
                (hasattr(item, "context") and item.context.get("schedule_id") == entry_dict.get("id"))
                for item in backlog
                if (isinstance(item, dict) and item.get("status") in ("pending", "in_progress")) or
                   (hasattr(item, "status") and item.status.value in ("pending", "in_progress"))
            )

            if existing:
                continue

            # Create work item from schedule entry (simplified model)
            work_item = WorkItem(
                id=str(uuid.uuid4()),
                item_type=WorkItemType.SCHEDULED,
                status=WorkItemStatus.PENDING,
                description=entry_dict.get("description", "Scheduled task"),
                context={
                    "schedule_id": entry_dict.get("id"),
                    "instruction": entry_dict.get("instruction"),
                    "repeat_minutes": entry_dict.get("repeat_minutes"),
                },
            )

            backlog.append(work_item)

        # Log schedule execution
        if due_ids:
            await state_manager.log_schedule_event({
                "event": "enqueue_due",
                "schedule_ids": due_ids,
                "timestamp": utcnow_isoformat(),
            })

        return {"backlog": backlog}

    return enqueue_due_node
