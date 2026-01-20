"""
WorktreePrep Node - Creates/checks out git worktree for build items.
"""

from pathlib import Path
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItemType
from ..tools.git import GitWorktreeManager


def create_node(worktrees_dir: Path) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the worktree prep node function."""
    git_manager = GitWorktreeManager(worktrees_dir)

    async def worktree_prep_node(state: ProgenitorState) -> ProgenitorState:
        """Prepare worktree for build items."""
        current_item = state.get("current_work_item")

        if not current_item:
            return {}

        # Check if this is a build item that needs a worktree
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            item_id = current_item.get("id", "unknown")
        else:
            item_type = current_item.item_type.value if hasattr(current_item.item_type, "value") else current_item.item_type
            item_id = current_item.id

        build_types = ["build_agent", "build_mcp", "upgrade"]

        if item_type not in build_types:
            # Not a build item - no worktree needed
            return {}

        try:
            # Create worktree
            worktree_path = await git_manager.create_worktree(item_id)

            # Update work item with worktree path
            if isinstance(current_item, dict):
                current_item["worktree_path"] = str(worktree_path)
            else:
                current_item.worktree_path = str(worktree_path)

            return {"current_work_item": current_item}

        except Exception as e:
            # Log error but continue - might not be in a git repo
            if isinstance(current_item, dict):
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["worktree_error"] = str(e)
            else:
                current_item.context["worktree_error"] = str(e)

            return {"current_work_item": current_item}

    return worktree_prep_node
