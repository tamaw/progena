"""
State Converters - Type-safe conversions between state representations.

Centralizes all state type conversions to ensure consistency and
reduce duplication across the codebase.
"""

import logging
from typing import Any, TypeVar, cast

from .models import (
    AgentRegistryEntry,
    ConversationMessage,
    Decision,
    DecisionAction,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


def to_work_item(value: Any) -> WorkItem | None:
    """Convert various formats to WorkItem.

    Args:
        value: A WorkItem, dict, or None

    Returns:
        WorkItem instance or None if value is None/invalid
    """
    if value is None:
        return None

    if isinstance(value, WorkItem):
        return value

    if isinstance(value, dict):
        try:
            # Handle item_type conversion
            item_type_str = value.get("item_type", "direct_answer")
            try:
                item_type = WorkItemType(item_type_str)
            except ValueError:
                item_type = WorkItemType.DIRECT_ANSWER

            # Handle status conversion
            status_str = value.get("status", "pending")
            try:
                status = WorkItemStatus(status_str)
            except ValueError:
                status = WorkItemStatus.PENDING

            return WorkItem(
                id=value.get("id", ""),
                item_type=item_type,
                status=status,
                description=value.get("description", ""),
                context=value.get("context", {}),
                target_agent=value.get("target_agent"),
                target_tool=value.get("target_tool"),
                worktree_path=value.get("worktree_path"),
                retry_count=value.get("retry_count", 0),
                max_retries=value.get("max_retries", 3),
                error_message=value.get("error_message"),
                result=value.get("result"),
            )
        except Exception as e:
            logger.warning("Failed to convert dict to WorkItem: %s", e)
            return None

    return None


def to_decision(value: Any) -> Decision | None:
    """Convert various formats to Decision.

    Args:
        value: A Decision, dict, or None

    Returns:
        Decision instance or None if value is None/invalid
    """
    if value is None:
        return None

    if isinstance(value, Decision):
        return value

    if isinstance(value, dict):
        try:
            # Handle action conversion
            action_str = value.get("action", "answer_directly")
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown DecisionAction %r; dropping decision", action_str)
                return None

            return Decision(
                action=action,
                reasoning=value.get("reasoning", ""),
                requires_planning=value.get("requires_planning", False),
                target_capabilities=value.get("target_capabilities", []),
                direct_response=value.get("direct_response"),
            )
        except Exception as e:
            logger.warning("Failed to convert dict to Decision: %s", e)
            return None

    return None


def to_schedule_entry(value: Any) -> ScheduleEntry | None:
    """Convert various formats to ScheduleEntry.

    Args:
        value: A ScheduleEntry, dict, or None

    Returns:
        ScheduleEntry instance or None if value is None/invalid
    """
    if value is None:
        return None

    if isinstance(value, ScheduleEntry):
        return value

    if isinstance(value, dict):
        try:
            return ScheduleEntry(**value)
        except Exception as e:
            logger.warning("Failed to convert dict to ScheduleEntry: %s", e)
            return None

    return None


def to_agent_registry_entry(value: Any) -> AgentRegistryEntry | None:
    """Convert various formats to AgentRegistryEntry.

    Args:
        value: An AgentRegistryEntry, dict, or None

    Returns:
        AgentRegistryEntry instance or None if value is None/invalid
    """
    if value is None:
        return None

    if isinstance(value, AgentRegistryEntry):
        return value

    if isinstance(value, dict):
        try:
            return AgentRegistryEntry(**value)
        except Exception as e:
            logger.warning("Failed to convert dict to AgentRegistryEntry: %s", e)
            return None

    return None


def to_conversation_message(value: Any) -> ConversationMessage | None:
    """Convert various formats to ConversationMessage.

    Args:
        value: A ConversationMessage, dict, or None

    Returns:
        ConversationMessage instance or None if value is None/invalid
    """
    if value is None:
        return None

    if isinstance(value, ConversationMessage):
        return value

    if isinstance(value, dict):
        try:
            return ConversationMessage(
                id=value.get("id", ""),
                role=value.get("role", "user"),
                content=value.get("content", ""),
                metadata=value.get("metadata", {}),
            )
        except Exception as e:
            logger.warning("Failed to convert dict to ConversationMessage: %s", e)
            return None

    return None


def to_ideal_state(value: Any) -> Any | None:
    """Convert dict to IdealState object if needed.

    Imports IdealState locally to avoid circular imports.

    Args:
        value: An IdealState, dict, or None

    Returns:
        IdealState instance or None if value is None/invalid
    """
    if value is None:
        return None

    # Import locally to avoid circular imports
    from .ideal_state import IdealState

    if isinstance(value, IdealState):
        return value

    if isinstance(value, dict):
        try:
            return IdealState(**value)
        except Exception as e:
            logger.warning("Failed to convert dict to IdealState: %s", e)
            return None

    return None


def to_dict(value: Any) -> dict[str, Any] | None:
    """Convert a Pydantic model or dict to dict.

    Args:
        value: A Pydantic model, dict, or None

    Returns:
        Dict representation or None if value is None
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return value

    if hasattr(value, "model_dump"):
        return cast(dict[str, Any], value.model_dump())

    return None


def ensure_list_of_work_items(values: Any) -> list[WorkItem]:
    """Ensure a list of values are all WorkItem instances.

    Args:
        values: A list of WorkItems, dicts, or mixed

    Returns:
        List of WorkItem instances (invalid items filtered out)
    """
    if not values:
        return []

    if not isinstance(values, list):
        values = [values]

    result = []
    for v in values:
        item = to_work_item(v)
        if item is not None:
            result.append(item)

    return result


def ensure_list_of_schedule_entries(values: Any) -> list[ScheduleEntry]:
    """Ensure a list of values are all ScheduleEntry instances.

    Args:
        values: A list of ScheduleEntries, dicts, or mixed

    Returns:
        List of ScheduleEntry instances (invalid items filtered out)
    """
    if not values:
        return []

    if not isinstance(values, list):
        values = [values]

    result = []
    for v in values:
        entry = to_schedule_entry(v)
        if entry is not None:
            result.append(entry)

    return result


def ensure_list_of_messages(values: Any) -> list[ConversationMessage]:
    """Ensure a list of values are all ConversationMessage instances.

    Args:
        values: A list of ConversationMessages, dicts, or mixed

    Returns:
        List of ConversationMessage instances (invalid items filtered out)
    """
    if not values:
        return []

    if not isinstance(values, list):
        values = [values]

    result = []
    for v in values:
        msg = to_conversation_message(v)
        if msg is not None:
            result.append(msg)

    return result
