"""
Backlog Manager - Business logic for work items.

ARCH-006: Split StateManager into focused responsibilities.

This module handles work item business logic:
- Adding/updating work items
- Finding actionable items
- Status transitions
- Priority management

For persistence, uses StatePersistence.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from .models import WorkItem, WorkItemStatus
from .persistence import StatePersistence
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


class BacklogManager:
    """Business logic for work item management.

    Handles:
    - Work item lifecycle (pending → in_progress → completed/failed)
    - Backlog queries (actionable items, by status, by type)
    - Priority-based ordering (FEAT-002)
    - Status transitions
    """

    BACKLOG_FILE = "backlog.json"

    def __init__(self, persistence: StatePersistence):
        """Initialize backlog manager.

        Args:
            persistence: StatePersistence instance for file operations.
        """
        self._persistence = persistence
        self._cache: Optional[list[WorkItem]] = None

    async def load(self) -> list[WorkItem]:
        """Load backlog from disk.

        Returns:
            List of work items.
        """
        async with self._persistence.file_lock("backlog"):
            data = await self._persistence.read(self.BACKLOG_FILE)
            if data is None:
                self._cache = []
                return []

            # data is list[Any], convert each dict to WorkItem using model_validate
            items: list[WorkItem] = []
            for item in data:
                if isinstance(item, dict):
                    items.append(WorkItem.model_validate(item))
            self._cache = items
            return self._cache

    async def save(self) -> None:
        """Save backlog to disk."""
        if self._cache is None:
            return

        async with self._persistence.file_lock("backlog"):
            data = [item.model_dump() for item in self._cache]
            await self._persistence.write(self.BACKLOG_FILE, data)

    async def get_all(self) -> list[WorkItem]:
        """Get all work items.

        Returns:
            List of all work items.
        """
        if self._cache is None:
            await self.load()
        return self._cache or []

    def get_actionable_items(self) -> list[WorkItem]:
        """Get items that can be worked on (pending or in_progress).

        Returns:
            List of actionable work items, sorted by priority.
        """
        if self._cache is None:
            return []

        actionable = [
            item for item in self._cache
            if item.is_actionable
        ]

        # Sort by effective priority (FEAT-002)
        return sorted(
            actionable,
            key=lambda x: self._effective_priority(x),
            reverse=True,
        )

    def _effective_priority(self, item: WorkItem) -> float:
        """Calculate effective priority including age boost.

        Per FEAT-002: priority + age_hours * 0.1

        Args:
            item: The work item.

        Returns:
            Effective priority score.
        """
        # Get explicit priority (default 0)
        priority = item.context.get("priority", 0)

        # Calculate age boost
        age = utcnow() - item.created_at
        age_hours = age.total_seconds() / 3600
        age_boost = age_hours * 0.1

        return float(priority) + age_boost

    def get_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        """Get items with a specific status.

        Args:
            status: The status to filter by.

        Returns:
            List of matching work items.
        """
        if self._cache is None:
            return []

        return [
            item for item in self._cache
            if item.status_value == status.value
        ]

    def find_by_id(self, item_id: str) -> Optional[WorkItem]:
        """Find a work item by ID.

        Args:
            item_id: The work item ID.

        Returns:
            The work item or None.
        """
        if self._cache is None:
            return None

        for item in self._cache:
            if item.id == item_id:
                return item
        return None

    async def add_item(self, item: WorkItem) -> None:
        """Add a work item to the backlog.

        Args:
            item: The work item to add.
        """
        if self._cache is None:
            await self.load()

        self._cache.append(item)  # type: ignore[union-attr]
        await self.save()

        logger.info(
            "Added work item %s: %s",
            item.id,
            item.description[:50],
        )

    async def update_item(self, item: WorkItem) -> None:
        """Update an existing work item.

        Args:
            item: The work item with updated fields.
        """
        if self._cache is None:
            await self.load()

        for i, existing in enumerate(self._cache or []):
            if existing.id == item.id:
                self._cache[i] = item  # type: ignore[index]
                break

        await self.save()

    async def mark_in_progress(self, item_id: str) -> Optional[WorkItem]:
        """Mark a work item as in progress.

        Args:
            item_id: The work item ID.

        Returns:
            The updated work item or None.
        """
        item = self.find_by_id(item_id)
        if item:
            item.mark_in_progress()
            await self.save()
        return item

    async def mark_completed(
        self,
        item_id: str,
        result: Optional[dict[str, Any]] = None,
    ) -> Optional[WorkItem]:
        """Mark a work item as completed.

        Args:
            item_id: The work item ID.
            result: Optional result data.

        Returns:
            The updated work item or None.
        """
        item = self.find_by_id(item_id)
        if item:
            item.mark_completed(result)
            await self.save()
            logger.info("Completed work item %s", item_id)
        return item

    async def mark_failed(
        self,
        item_id: str,
        error: str,
    ) -> Optional[WorkItem]:
        """Mark a work item as failed.

        Args:
            item_id: The work item ID.
            error: Error message.

        Returns:
            The updated work item or None.
        """
        item = self.find_by_id(item_id)
        if item:
            item.mark_failed(error)
            await self.save()
            logger.warning(
                "Work item %s failed: %s (retry %d/%d)",
                item_id,
                error[:100],
                item.retry_count,
                item.max_retries,
            )
        return item

    async def remove_item(self, item_id: str) -> bool:
        """Remove a work item from the backlog.

        Args:
            item_id: The work item ID.

        Returns:
            True if removed, False if not found.
        """
        if self._cache is None:
            await self.load()

        original_len = len(self._cache or [])
        self._cache = [  # type: ignore[assignment]
            item for item in (self._cache or [])
            if item.id != item_id
        ]

        if len(self._cache) < original_len:
            await self.save()
            return True
        return False

    def get_stats(self) -> dict[str, int]:
        """Get backlog statistics.

        Returns:
            Dict with counts by status.
        """
        if self._cache is None:
            return {"total": 0}

        stats = {"total": len(self._cache)}
        for status in WorkItemStatus:
            stats[status.value] = len(self.get_by_status(status))

        return stats
