"""
CommitCore Node - Persists state with locks and appends log events.
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
        # Save backlog
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

        # Save schedule
        schedule = state.get("schedule", [])
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

        await state_manager.save_schedule(schedule_entries)

        # Log validation result if present
        validation_result = state.get("last_validation_result")
        if validation_result:
            await state_manager.log_validation(validation_result)

        # Update current state
        core_state = {
            "cycle_count": state.get("cycle_count", 0),
            "actions_this_cycle": state.get("actions_this_cycle", 0),
            "run_id": state.get("run_id"),
            "last_commit": utcnow_isoformat(),
        }
        await state_manager.save_state(core_state)

        return {
            "last_commit_time": utcnow(),
        }

    return commit_core_node
