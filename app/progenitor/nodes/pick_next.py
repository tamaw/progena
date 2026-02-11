"""
PickNext Node - Selects the next backlog item to process.
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from ..state.models import ProgenitorState, WorkItem

logger = logging.getLogger(__name__)


def create_node() -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the pick next node function."""

    async def pick_next_node(state: ProgenitorState) -> ProgenitorState:
        """Select the next work item from the backlog."""
        logger.info("PICK_NEXT: Starting pick_next_node")
        backlog = state.get("backlog", [])
        logger.info(f"PICK_NEXT: Backlog has {len(backlog)} items")

        # Find first pending item
        for i, item in enumerate(backlog):
            work_item = _to_work_item(item)
            logger.debug(f"PICK_NEXT: Item {i}: type={type(item).__name__}, work_item={work_item is not None}")

            if work_item and work_item.is_pending:
                logger.info(f"PICK_NEXT: Found pending item: id={work_item.id}, type={work_item.item_type}")
                work_item.mark_in_progress()

                # Update in backlog (preserving same reference structure)
                updated_backlog = []
                for b_item in backlog:
                    b_id = b_item.id if isinstance(b_item, WorkItem) else b_item.get("id") if isinstance(b_item, dict) else None
                    if b_id == work_item.id:
                        updated_backlog.append(work_item)
                    else:
                        updated_backlog.append(b_item)

                return {
                    "current_work_item": work_item,
                    "backlog": updated_backlog,
                }

        # No pending items
        logger.info("PICK_NEXT: No pending items found in backlog")
        return {"current_work_item": None}

    return pick_next_node


def _to_work_item(item) -> WorkItem | None:
    """Convert item to WorkItem if possible."""
    if isinstance(item, WorkItem):
        return item
    if isinstance(item, dict):
        try:
            from ..state.models import WorkItemStatus, WorkItemType
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
                submodule_path=item.get("submodule_path"),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to convert item to WorkItem: %s", e)
            return None
    return None
