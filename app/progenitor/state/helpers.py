"""
State Helpers - Utility functions for handling state type conversions.

These helpers normalize between dict and Pydantic model representations,
which is common when working with LangGraph state that can be updated
partially with dicts.
"""

import logging
from typing import Any, Optional, TypeVar, Type, TYPE_CHECKING, Mapping
from .models import (
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    Decision,
    DecisionAction,
    ScheduleEntry,
    AgentRegistryEntry,
    ConversationMessage,
    BuildPhases,
)

if TYPE_CHECKING:
    from .ideal_state import IdealState

logger = logging.getLogger(__name__)

T = TypeVar('T')


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
        return item.is_actionable

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
        run_at = item.get("run_at")
        if run_at is None:
            return None

        return ScheduleEntry(
            id=item.get("id", "unknown"),
            description=item.get("description", ""),
            instruction=item.get("instruction", ""),
            run_at=run_at,
            repeat_minutes=item.get("repeat_minutes"),
            enabled=item.get("enabled", True),
            last_run=item.get("last_run"),
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
    return updated_phases


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


# ============================================================================
# Conversation History Management (TD-008)
# ============================================================================

# Default limits for conversation history
DEFAULT_MAX_MESSAGES = 100  # Keep last N messages
DEFAULT_MAX_TOKENS_ESTIMATE = 50000  # Approximate token limit for context


def trim_conversation_history(
    messages: list,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    preserve_system: bool = True,
) -> list:
    """Trim conversation history to prevent unbounded memory growth.

    Uses a sliding window approach, keeping the most recent messages.
    Optionally preserves system messages at the start.

    Args:
        messages: List of ConversationMessage objects or dicts
        max_messages: Maximum number of messages to keep
        preserve_system: If True, always keep system messages at start

    Returns:
        Trimmed list of messages
    """
    if not messages or len(messages) <= max_messages:
        return messages

    if preserve_system:
        # Find system messages at the start
        system_messages = []
        other_messages = []

        for msg in messages:
            # Handle various message formats: objects, dicts, or raw strings
            if hasattr(msg, "role"):
                role = msg.role
            elif isinstance(msg, dict):
                role = msg.get("role", "")
            else:
                # Skip or treat non-standard messages as non-system
                other_messages.append(msg)
                continue

            if role == "system" and not other_messages:
                system_messages.append(msg)
            else:
                other_messages.append(msg)

        # Keep system messages + most recent other messages
        remaining_slots = max_messages - len(system_messages)
        if remaining_slots > 0:
            return system_messages + other_messages[-remaining_slots:]
        else:
            # Even system messages exceed limit, keep most recent
            return messages[-max_messages:]

    return messages[-max_messages:]


def summarize_old_messages(
    messages: list,
    summary_threshold: int = 50,
) -> tuple[list, Optional[str]]:
    """Create a summary of old messages when history grows large.

    This is an alternative to simple truncation - creates a condensed
    summary of older messages that can be prepended to context.

    Args:
        messages: List of ConversationMessage objects or dicts
        summary_threshold: Number of messages before summarizing older ones

    Returns:
        Tuple of (trimmed_messages, summary_text or None)
    """
    if len(messages) <= summary_threshold:
        return messages, None

    # Keep recent messages, summarize older ones
    keep_count = summary_threshold // 2
    old_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]

    # Create simple summary of old conversation
    summary_parts = []
    for msg in old_messages[-10:]:  # Summarize last 10 of old messages
        role = msg.role if hasattr(msg, "role") else msg.get("role", "")
        content = msg.content if hasattr(msg, "content") else msg.get("content", "")
        if content:
            # Truncate long content
            short_content = content[:100] + "..." if len(content) > 100 else content
            summary_parts.append(f"[{role}]: {short_content}")

    if summary_parts:
        summary = f"[Earlier conversation summary - {len(old_messages)} messages]:\n" + "\n".join(summary_parts)
        return recent_messages, summary

    return recent_messages, None


def ensure_ideal_state(ideal_state: Any, allow_none: bool = True) -> Optional["IdealState"]:
    """Convert ideal_state dict to IdealState object if needed.

    This is the canonical helper for IdealState conversion - use this
    instead of duplicating the conversion logic in multiple places.

    LangGraph state may serialize IdealState to dict when passing through nodes.
    This helper ensures we always have an IdealState object for methods.

    Args:
        ideal_state: IdealState, dict, or None
        allow_none: If True, returns None for None/invalid input.
                    If False, raises TypeError for None/invalid input.

    Returns:
        IdealState instance, or None if allow_none=True and input is None/invalid

    Raises:
        TypeError: If allow_none=False and ideal_state is not valid
    """
    # Late import to avoid circular imports
    from .ideal_state import IdealState

    if ideal_state is None:
        if allow_none:
            return None
        raise TypeError("ideal_state cannot be None")

    if isinstance(ideal_state, IdealState):
        return ideal_state

    if isinstance(ideal_state, dict):
        try:
            return IdealState(**ideal_state)
        except Exception as e:
            logger.warning("Failed to convert ideal_state dict to IdealState: %s", e)
            if allow_none:
                return None
            raise TypeError(f"Invalid ideal_state dict: {e}") from e

    if allow_none:
        logger.warning("Unknown ideal_state type: %s", type(ideal_state))
        return None
    raise TypeError(f"Expected IdealState or dict, got {type(ideal_state)}")


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
            item_id = item.id if hasattr(item, "id") else item.get("id", "unknown") if isinstance(item, dict) else "unknown"
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
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
        if hasattr(item, "id") and getattr(item, "id") == item_id:
            return item
    return None
