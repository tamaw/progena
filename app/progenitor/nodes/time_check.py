"""
TimeCheck Node - Reads current time and identifies due scheduled items.
"""

from datetime import datetime
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState
from ..runtime.scheduler import Scheduler
from ..runtime.time_utils import utcnow


def create_node(timezone: str = "Australia/Melbourne") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the time check node function.

    Args:
        timezone: Timezone for time calculations (from config.runtime.timezone).
    """
    scheduler = Scheduler(timezone=timezone)

    async def time_check_node(state: ProgenitorState) -> ProgenitorState:
        """Check current time and find due scheduled items."""
        current_time = utcnow()
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
        due_ids = [item.get("id") for item in due_items if item.get("id")]

        return {
            "current_time": current_time,
            "due_items": due_ids,
        }

    return time_check_node
