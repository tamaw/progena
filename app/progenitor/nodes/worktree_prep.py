"""
WorktreePrep Node - Creates/checks out git submodule for build items.

BL-007: Updated to use git submodules instead of worktrees for build isolation.
Each agent gets its own git submodule for isolated commits.
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
        """Prepare git submodule for build items.

        BL-007: Initialize git submodule for each build based on build_settings.
        Uses project_directory from build_settings for consistent isolation.
        """
        logger.info("WORKTREE_PREP: Starting worktree_prep_node")
        current_item = state.get("current_work_item")

        if not current_item:
            logger.info("WORKTREE_PREP: No current_work_item, returning")
            return {}

        # Check if this is a build item that needs a git repo
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            item_id = current_item.get("id", "unknown")
        else:
            item_type = current_item.item_type.value if hasattr(current_item.item_type, "value") else current_item.item_type
            item_id = current_item.id

        build_types = ["build_agent", "build_mcp", "upgrade"]

        logger.info(f"WORKTREE_PREP: item_type={item_type}, item_id={item_id}")

        if item_type not in build_types:
            # Not a build item - no git repo needed
            logger.info(f"WORKTREE_PREP: {item_type} not in build_types, skipping git setup")
            return {}

        try:
            # BL-007: Get project_directory from build_settings
            build_settings = state.get("build_settings")
            project_directory = None

            if build_settings:
                if isinstance(build_settings, dict):
                    project_directory = build_settings.get("project_directory")
                    project_name = build_settings.get("project_name", "unknown")
                elif hasattr(build_settings, "project_directory"):
                    project_directory = build_settings.project_directory
                    project_name = getattr(build_settings, "project_name", "unknown")
                else:
                    project_name = "unknown"
            else:
                project_name = "unknown"

            if project_directory:
                # BL-007: Initialize git submodule for the agent
                logger.info(f"WORKTREE_PREP: Initializing git submodule at {project_directory}")

                submodule_path = Path(project_directory)
                success, message = await git_manager.init_submodule(
                    submodule_path=submodule_path,
                    submodule_name=project_name,
                )

                # Update work item with submodule info
                if isinstance(current_item, dict):
                    current_item["submodule_path"] = str(submodule_path)
                    current_item["context"] = current_item.get("context", {})
                    current_item["context"]["submodule_initialized"] = success
                    current_item["context"]["submodule_message"] = message
                else:
                    current_item.submodule_path = str(submodule_path)
                    current_item.context["submodule_initialized"] = success
                    current_item.context["submodule_message"] = message

                logger.info(f"WORKTREE_PREP: Submodule init result: success={success}, message={message}")

            else:
                # No build_settings - fall back to main repo
                logger.info("WORKTREE_PREP: No project_directory in build_settings, using main repo")

                if isinstance(current_item, dict):
                    current_item["submodule_path"] = None
                    current_item["context"] = current_item.get("context", {})
                    current_item["context"]["submodule_skipped"] = True
                else:
                    current_item.submodule_path = None
                    current_item.context["submodule_skipped"] = True

            return {"current_work_item": current_item}

        except Exception as e:
            # Log error but continue - might not be in a git repo
            logger.error(f"WORKTREE_PREP: Failed to initialize submodule: {e}")
            if isinstance(current_item, dict):
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["submodule_error"] = str(e)
            else:
                current_item.context["submodule_error"] = str(e)

            return {"current_work_item": current_item}

    return worktree_prep_node
