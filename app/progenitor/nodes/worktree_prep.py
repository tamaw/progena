"""
WorktreePrep Node - Creates/checks out git worktree for build items.
"""

import logging
from pathlib import Path
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItemType
from ..tools.git import GitWorktreeManager

logger = logging.getLogger(__name__)


def create_node(worktrees_dir: Path) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the worktree prep node function."""
    git_manager = GitWorktreeManager(worktrees_dir)

    async def worktree_prep_node(state: ProgenitorState) -> ProgenitorState:
        """Prepare worktree for build items."""
        logger.info("WORKTREE_PREP: Starting worktree_prep_node")
        current_item = state.get("current_work_item")

        if not current_item:
            logger.info("WORKTREE_PREP: No current_work_item, returning")
            return {}

        # Check if this is a build item that needs a worktree
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            item_id = current_item.get("id", "unknown")
        else:
            item_type = current_item.item_type.value if hasattr(current_item.item_type, "value") else current_item.item_type
            item_id = current_item.id

        build_types = ["build_agent", "build_mcp", "upgrade"]

        logger.info(f"WORKTREE_PREP: item_type={item_type}, item_id={item_id}")

        if item_type not in build_types:
            # Not a build item - no worktree needed
            logger.info(f"WORKTREE_PREP: {item_type} not in build_types, skipping worktree")
            return {}

        try:
            # For now, skip worktree creation and just use the main repo
            # Worktrees add complexity and require clean git state
            # We'll use app/agents or app/mcp as the working directory
            logger.info(f"WORKTREE_PREP: Using main repo for builds (worktree creation skipped)")

            # Update work item to indicate no worktree
            if isinstance(current_item, dict):
                current_item["worktree_path"] = None
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["worktree_skipped"] = True
            else:
                current_item.worktree_path = None
                current_item.context["worktree_skipped"] = True

            return {"current_work_item": current_item}

        except Exception as e:
            # Log error but continue - might not be in a git repo
            logger.error(f"WORKTREE_PREP: Failed to create worktree: {e}")
            if isinstance(current_item, dict):
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["worktree_error"] = str(e)
            else:
                current_item.context["worktree_error"] = str(e)

            return {"current_work_item": current_item}

    return worktree_prep_node
