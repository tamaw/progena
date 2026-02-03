"""
CommitCore Node - Persists state with locks and appends log events.

State is split across files:
- build_state.json: Agent builds only (inner loop, backlog of build items)
- current_state.json: Progenitor agent state (chat, tool calls) - user session
- schedule.json: Scheduled tasks (system feature, persists beyond user sessions)
"""

import logging
from datetime import datetime
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState
from ..runtime.time_utils import utcnow, utcnow_isoformat

logger = logging.getLogger(__name__)


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the commit core node function."""

    async def commit_core_node(state: ProgenitorState) -> ProgenitorState:
        """Persist core state files and log events."""
        # Save backlog to build_state.json (build items only)
        backlog = state.get("backlog", [])
        backlog_data = []
        for item in backlog:
            if hasattr(item, "model_dump"):
                backlog_data.append(item.model_dump())
            elif isinstance(item, dict):
                backlog_data.append(item)

        from ..state.models import WorkItem
        work_items = []
        for item_data in backlog_data:
            try:
                work_items.append(WorkItem(**item_data))
            except (TypeError, ValueError) as e:
                logger.debug("Skipping invalid backlog item: %s", e)

        await state_manager.save_backlog(work_items)

        # Handle new schedule entry (from planner when scheduling a task)
        new_schedule_entry = state.get("new_schedule_entry")

        # Save schedule - merge with any new entries
        schedule = list(state.get("schedule", []))

        # Add new entry if present (from planner)
        if new_schedule_entry:
            if hasattr(new_schedule_entry, "model_dump"):
                schedule.append(new_schedule_entry)
            elif isinstance(new_schedule_entry, dict):
                from ..state.models import ScheduleEntry
                try:
                    schedule.append(ScheduleEntry(**new_schedule_entry))
                except (TypeError, ValueError) as e:
                    logger.warning("Failed to add new schedule entry: %s", e)

        schedule_data = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                schedule_data.append(item.model_dump())
            elif isinstance(item, dict):
                schedule_data.append(item)

        from ..state.models import ScheduleEntry
        schedule_entries = []
        for item_data in schedule_data:
            try:
                schedule_entries.append(ScheduleEntry(**item_data))
            except (TypeError, ValueError) as e:
                logger.debug("Skipping invalid schedule entry: %s", e)

        # Save schedule to schedule.json only (system feature, not user session)
        await state_manager.save_schedule(schedule_entries)

        # Log validation result if present (V2 only)
        validation_result = state.get("validation_result_v2")
        if validation_result:
            await state_manager.log_validation(validation_result)

        # Update current_state.json (progenitor agent operational state)
        current_state_update = {
            "cycle_count": state.get("cycle_count", 0),
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "run_id": state.get("run_id"),
            "last_checkpoint": utcnow_isoformat(),
        }
        await state_manager.update_current_state(current_state_update)

        # Return updated schedule so state reflects the new entry
        return {
            "last_commit_time": utcnow(),
            "schedule": schedule_entries,  # Updated schedule with new entry
            "new_schedule_entry": None,  # Clear the pending entry
        }

    return commit_core_node
