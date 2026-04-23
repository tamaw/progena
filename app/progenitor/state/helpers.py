"""
State Helpers - Utility functions for handling state type conversions.

These helpers normalize between dict and Pydantic model representations,
which is common when working with LangGraph state that can be updated
partially with dicts.
"""

import logging
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from .models import (
    BuildPhases,
    Decision,
    DecisionAction,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_work_item_actionable(item: Any) -> bool:
    """Check if a work item is actionable (pending or in_progress).

    This is the canonical implementation - use this instead of duplicating
    the check logic. Handles WorkItem objects, dicts, and objects with
    a status attribute.

    Args:
        item: WorkItem, dict with 'status' key, or object with status attribute

    Returns:
        True if the item can be acted on (pending or in_progress)
    """
    if item is None:
        return False

    # WorkItem model - use its property
    if isinstance(item, WorkItem):
        return item.is_actionable

    # Dict representation
    if isinstance(item, dict):
        status = item.get("status", "pending")
        return status in ("pending", "in_progress")

    # Generic object with status attribute
    if hasattr(item, "is_actionable"):
        return cast(bool, item.is_actionable)

    if hasattr(item, "status"):
        status = item.status
        if hasattr(status, "value"):
            status = status.value
        return status in ("pending", "in_progress")

    return False


def get_actionable_items(backlog: list) -> list:
    """Filter backlog to only actionable items.

    Args:
        backlog: List of work items (WorkItem, dict, or mixed)

    Returns:
        List of items that are actionable (pending or in_progress)
    """
    return [item for item in backlog if is_work_item_actionable(item)]


def normalize_work_item(item: Any) -> WorkItem | None:
    """Convert various formats to WorkItem.

    Args:
        item: A WorkItem, dict, or None

    Returns:
        WorkItem instance or None if item is None

    Raises:
        ValidationError: If a dict payload does not satisfy WorkItem validation
    """
    if item is None:
        return None

    if isinstance(item, WorkItem):
        return item

    if isinstance(item, dict):
        return WorkItem.model_validate(item)

    return None


def normalize_decision(item: Any) -> Decision | None:
    """Convert various formats to Decision.

    Args:
        item: A Decision, dict, or None

    Returns:
        Decision instance or None if item is None/invalid
    """
    if item is None:
        return None

    if isinstance(item, Decision):
        return item

    if isinstance(item, dict):
        action_str = item.get("action", "answer_directly")
        try:
            action = DecisionAction(action_str)
        except ValueError:
            logger.warning("Unknown DecisionAction %r; dropping decision", action_str)
            return None

        return Decision(
            action=action,
            reasoning=item.get("reasoning", ""),
            requires_planning=item.get("requires_planning", False),
            target_capabilities=item.get("target_capabilities", []),
            direct_response=item.get("direct_response"),
        )

    return None


def normalize_schedule_entry(item: Any) -> ScheduleEntry | None:
    """Convert various formats to ScheduleEntry.

    Args:
        item: A ScheduleEntry, dict, or None

    Returns:
        ScheduleEntry instance or None if item is None/invalid
    """
    if item is None:
        return None

    if isinstance(item, ScheduleEntry):
        return item

    if isinstance(item, dict):
        cron_expression = item.get("cron_expression")
        message = item.get("message")
        created_at = item.get("created_at")
        if cron_expression is None or message is None or created_at is None:
            return None

        return ScheduleEntry(
            id=item.get("id", "unknown"),
            message=message,
            cron_expression=cron_expression,
            created_at=created_at,
            last_run=item.get("last_run"),
            last_fired_at=item.get("last_fired_at"),
        )

    return None


def to_dict(item: Any) -> dict[str, Any]:
    """Convert a Pydantic model or dict to dict.

    Args:
        item: A Pydantic model or dict

    Returns:
        Dict representation
    """
    if item is None:
        return {}

    if hasattr(item, "model_dump"):
        return cast(dict[str, Any], item.model_dump())

    if isinstance(item, dict):
        return item

    # Try __dict__ as fallback
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)

    return {}


def get_ideal_state(state: Mapping[str, Any]) -> Any:
    """Get ideal_state from phases.observe in state."""
    phases = state.get("phases") or {}
    observe_phase = phases.get("observe") or {}
    return observe_phase.get("ideal_state")


def set_ideal_state(phases: BuildPhases | dict[str, Any] | None, ideal_state: Any) -> BuildPhases:
    """Return phases with observe.ideal_state set, preserving other phases."""
    if hasattr(ideal_state, "model_dump"):
        ideal_state = ideal_state.model_dump()
    updated_phases: dict[str, Any] = dict(phases or {})
    observe_phase: dict[str, Any] = dict(updated_phases.get("observe") or {})
    observe_phase["ideal_state"] = ideal_state
    updated_phases["observe"] = observe_phase
    return cast(BuildPhases, updated_phases)


def is_actionable(item: Any) -> bool:
    """Check if a work item is actionable (pending or in_progress).

    Args:
        item: A WorkItem, dict, or object with status attribute

    Returns:
        True if actionable
    """
    if isinstance(item, WorkItem):
        return item.is_actionable

    if isinstance(item, dict):
        status = item.get("status", "")
        return status in ("pending", "in_progress")

    if hasattr(item, "status"):
        status = item.status
        if hasattr(status, "value"):
            status = status.value
        return status in ("pending", "in_progress")

    return False


def get_status_value(item: Any) -> str:
    """Get the status value as string from various formats.

    Args:
        item: A WorkItem, dict, or object with status attribute

    Returns:
        Status as string
    """
    if isinstance(item, WorkItem):
        return item.status_value

    if isinstance(item, dict):
        status = item.get("status", "unknown")
        if hasattr(status, "value"):
            return cast(str, status.value)
        return str(status)

    if hasattr(item, "status"):
        status = item.status
        if hasattr(status, "value"):
            return cast(str, status.value)
        return str(status)

    return "unknown"


def get_item_type_value(item: Any) -> str:
    """Get the item_type value as string from various formats.

    Args:
        item: A WorkItem, dict, or object with item_type attribute

    Returns:
        Item type as string
    """
    if isinstance(item, WorkItem):
        return item.item_type_value

    if isinstance(item, dict):
        item_type = item.get("item_type", "unknown")
        if hasattr(item_type, "value"):
            return cast(str, item_type.value)
        return str(item_type)

    if hasattr(item, "item_type"):
        item_type = item.item_type
        if hasattr(item_type, "value"):
            return cast(str, item_type.value)
        return str(item_type)

    return "unknown"


def mark_work_item_completed(
    item: Any,
    result: dict,
    timestamp: Any = None,
) -> Any:
    """Mark a single work item as completed.

    Handles WorkItem models, dicts, and objects with status attribute.
    Returns the updated item (mutates in place for models/dicts).

    Args:
        item: WorkItem, dict, or object with status attribute
        result: Result data to attach to the item
        timestamp: Optional timestamp (defaults to utcnow())

    Returns:
        The updated item (same object, mutated)
    """
    from ..runtime.time_utils import utcnow

    if timestamp is None:
        timestamp = utcnow()

    if isinstance(item, WorkItem):
        item.status = WorkItemStatus.COMPLETED
        item.result = result
        item.updated_at = timestamp
        return item

    if isinstance(item, dict):
        item["status"] = "completed"
        item["result"] = result
        if hasattr(timestamp, "isoformat"):
            item["updated_at"] = timestamp.isoformat()
        else:
            item["updated_at"] = str(timestamp)
        return item

    # Generic object with status attribute
    if hasattr(item, "status"):
        item.status = WorkItemStatus.COMPLETED if hasattr(item.status, "value") else "completed"
        if hasattr(item, "result"):
            item.result = result
        if hasattr(item, "updated_at"):
            item.updated_at = timestamp

    return item


def mark_actionable_items_completed(
    backlog: list,
    result: dict,
    timestamp: Any = None,
) -> list:
    """Mark all actionable (pending/in_progress) items in backlog as completed.

    This is used by the BUILD phase to mark items as completed once the
    copilot run finishes, regardless of test results.

    Args:
        backlog: List of work items
        result: Result data to attach to completed items
        timestamp: Optional timestamp (defaults to utcnow())

    Returns:
        Updated backlog with actionable items marked as completed
    """
    from ..runtime.time_utils import utcnow

    if timestamp is None:
        timestamp = utcnow()

    updated_backlog = []
    for item in backlog:
        if is_work_item_actionable(item):
            mark_work_item_completed(item, result, timestamp)
            if isinstance(item, WorkItem):
                item_id = item.id
            elif isinstance(item, dict):
                item_id = item.get("id", "unknown")
            else:
                item_id = getattr(item, "id", "unknown")
            logger.info(f"Marked work item {item_id} as completed")
        updated_backlog.append(item)

    return updated_backlog


def find_work_item_by_id(backlog: list, item_id: str) -> Any:
    """Find a work item in the backlog by ID.

    Args:
        backlog: List of work items
        item_id: ID to search for

    Returns:
        The work item if found, None otherwise
    """
    for item in backlog:
        if isinstance(item, WorkItem) and item.id == item_id:
            return item
        if isinstance(item, dict):
            if item.get("id") == item_id:
                return item
            continue
        if getattr(item, "id", None) == item_id:
            return item
    return None
