"""TimeCheck Node - Reads current time, identifies due scheduled items, and enqueues them.

Merged from time_check + enqueue_due for simplicity (they always run back-to-back).
"""

import uuid
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItem, WorkItemType, WorkItemStatus
from ..runtime.scheduler import Scheduler
from ..runtime.time_utils import utcnow, utcnow_isoformat


def create_node(timezone: str = "Australia/Melbourne", state_manager=None) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the time check node function.

    Args:
        timezone: Timezone for time calculations (from config.runtime.timezone).
        state_manager: StateManager for logging schedule events.
    """
    scheduler = Scheduler(timezone=timezone)

    async def time_check_node(state: ProgenitorState) -> ProgenitorState:
        """Check current time, find due scheduled items, and enqueue them.

        This node combines time checking and enqueueing into a single step:
        1. Get current time
        2. Find due scheduled items
        3. Create work items for due schedules and add to backlog

        Optimization: Skip scheduling checks for user input cycles - background loop handles this.
        """
        current_time = utcnow()

        # Skip scheduling checks for user input - background loop handles scheduled tasks
        # User cycles should be fast and focused on responding to the user
        if state.get("current_input"):
            return {
                "current_time": current_time,
                "due_items": [],
            }

        schedule = state.get("schedule", [])

        # Convert schedule entries to dicts if needed
        schedule_dicts = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                schedule_dicts.append(item.model_dump())
            elif isinstance(item, dict):
                schedule_dicts.append(item)

        # Find due items
        due_items = scheduler.get_due_items(schedule_dicts, current_time)
        due_ids: list[str] = [str(item.get("id")) for item in due_items if item.get("id") is not None]

        # If no due items, return early with just time update
        if not due_ids:
            return {
                "current_time": current_time,
                "due_items": [],
            }

        # Enqueue due items as work items
        backlog = list(state.get("backlog", []))

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

            # Create work item from schedule entry
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
        if due_ids and state_manager:
            await state_manager.log_schedule_event({
                "event": "enqueue_due",
                "schedule_ids": due_ids,
                "timestamp": utcnow_isoformat(),
            })

        return {
            "current_time": current_time,
            "due_items": due_ids,
            "backlog": backlog,
        }

    return time_check_node
