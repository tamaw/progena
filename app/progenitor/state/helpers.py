"""
State Helpers - Utility functions for handling state type conversions.

These helpers normalize between dict and Pydantic model representations,
which is common when working with LangGraph state that can be updated
partially with dicts.
"""

from typing import Any, Optional, TypeVar, Type
from .models import (
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    Decision,
    DecisionAction,
    ScheduleEntry,
    AgentRegistryEntry,
    ConversationMessage,
)

T = TypeVar('T')


def normalize_work_item(item: Any) -> Optional[WorkItem]:
    """Convert various formats to WorkItem.

    Args:
        item: A WorkItem, dict, or None

    Returns:
        WorkItem instance or None if item is None/invalid
    """
    if item is None:
        return None

    if isinstance(item, WorkItem):
        return item

    if isinstance(item, dict):
        item_type_str = item.get("item_type", "direct_answer")
        try:
            item_type = WorkItemType(item_type_str)
        except ValueError:
            item_type = WorkItemType.DIRECT_ANSWER

        status_str = item.get("status", "pending")
        try:
            status = WorkItemStatus(status_str)
        except ValueError:
            status = WorkItemStatus.PENDING

        return WorkItem(
            id=item.get("id", "unknown"),
            item_type=item_type,
            status=status,
            description=item.get("description", ""),
            context=item.get("context", {}),
            target_agent=item.get("target_agent"),
            target_tool=item.get("target_tool"),
            worktree_path=item.get("worktree_path"),
            retry_count=item.get("retry_count", 0),
            max_retries=item.get("max_retries", 3),
            error_message=item.get("error_message"),
            result=item.get("result"),
        )

    return None


def normalize_decision(item: Any) -> Optional[Decision]:
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
            action = DecisionAction.ANSWER_DIRECTLY

        return Decision(
            action=action,
            reasoning=item.get("reasoning", ""),
            requires_planning=item.get("requires_planning", False),
            target_capabilities=item.get("target_capabilities", []),
            direct_response=item.get("direct_response"),
        )

    return None


def normalize_schedule_entry(item: Any) -> Optional[ScheduleEntry]:
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
        action_str = item.get("action_type", "answer_directly")
        try:
            action_type = DecisionAction(action_str)
        except ValueError:
            action_type = DecisionAction.ANSWER_DIRECTLY

        return ScheduleEntry(
            id=item.get("id", "unknown"),
            description=item.get("description", ""),
            cron_expression=item.get("cron_expression"),
            interval_minutes=item.get("interval_minutes"),
            run_at=item.get("run_at"),
            enabled=item.get("enabled", True),
            action_type=action_type,
            action_config=item.get("action_config", {}),
            last_run=item.get("last_run"),
            next_run=item.get("next_run"),
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
        return item.model_dump()

    if isinstance(item, dict):
        return item

    # Try __dict__ as fallback
    if hasattr(item, "__dict__"):
        return dict(item.__dict__)

    return {}


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
            return status.value
        return str(status)

    if hasattr(item, "status"):
        status = item.status
        if hasattr(status, "value"):
            return status.value
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
            return item_type.value
        return str(item_type)

    if hasattr(item, "item_type"):
        item_type = item.item_type
        if hasattr(item_type, "value"):
            return item_type.value
        return str(item_type)

    return "unknown"
