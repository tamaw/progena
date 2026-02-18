"""
WorktreePrep Node - Creates git worktree and venv for build items.

Each build item gets an isolated git worktree under /worktrees with its own
Python virtual environment built from the agent/mcp's requirements.txt.
"""

import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from ..state.models import ProgenitorState
from ..tools.git import GitWorktreeManager

logger = logging.getLogger(__name__)


async def _setup_worktree_venv(agent_dir: Path) -> bool:
    """Create a venv inside the agent/mcp directory and install its requirements.

    Args:
        agent_dir: Path to the agent package inside the worktree
                   (e.g. worktrees/<id>/app/agents/my_agent).

    Returns:
        True if venv was created and deps installed, False on error.
    """
    requirements = agent_dir / "requirements.txt"
    if not requirements.exists():
        logger.info("WORKTREE_PREP: No requirements.txt in %s, skipping venv", agent_dir)
        return True

    venv_dir = agent_dir / ".venv"
    logger.info("WORKTREE_PREP: Creating venv at %s", venv_dir)

    # Create venv
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "venv", str(venv_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("WORKTREE_PREP: venv creation failed: %s", stderr.decode()[:500])
        return False

    # Determine pip path
    if sys.platform == "win32":
        pip = venv_dir / "Scripts" / "pip"
    else:
        pip = venv_dir / "bin" / "pip"

    # Install requirements
    logger.info("WORKTREE_PREP: Installing requirements from %s", requirements)
    proc = await asyncio.create_subprocess_exec(
        str(pip), "install", "-r", str(requirements),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error("WORKTREE_PREP: pip install failed: %s", stderr.decode()[:500])
        return False

    logger.info("WORKTREE_PREP: Venv ready at %s", venv_dir)
    return True


def create_node(worktrees_dir: Path) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the worktree prep node function."""
    git_manager = GitWorktreeManager(worktrees_dir)

    async def worktree_prep_node(state: ProgenitorState) -> ProgenitorState:
        """Create a git worktree and isolated venv for the current build item."""
        logger.info("WORKTREE_PREP: Starting")
        current_item = state.get("current_work_item")

        if not current_item:
            logger.info("WORKTREE_PREP: No current_work_item, skipping")
            return {}

        # Extract item metadata
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            item_id = current_item.get("id", "unknown")
        else:
            item_type = current_item.item_type
            item_id = current_item.id

        build_types = ["build_agent", "build_tool", "upgrade"]
        logger.info("WORKTREE_PREP: item_type=%s, item_id=%s", item_type, item_id)

        if item_type not in build_types:
            logger.info("WORKTREE_PREP: %s is not a build item, skipping", item_type)
            return {}

        build_settings = state.get("build_settings")
        project_directory = None

        if build_settings:
            if isinstance(build_settings, dict):
                project_directory = build_settings.get("project_directory")
            elif hasattr(build_settings, "project_directory"):
                project_directory = build_settings.project_directory

        if not project_directory:
            logger.warning("WORKTREE_PREP: No project_directory in build_settings, skipping")
            return {}

        try:
            # Create worktree with a branch named after the work item
            branch_name = f"progenitor/build/{item_id}"
            worktree_path = await git_manager.create_worktree(
                work_item_id=item_id,
                branch_name=branch_name,
            )
            logger.info("WORKTREE_PREP: Created worktree at %s on branch %s", worktree_path, branch_name)

            # Build the agent venv inside the worktree
            agent_dir = worktree_path / project_directory
            if agent_dir.exists():
                venv_ok = await _setup_worktree_venv(agent_dir)
            else:
                logger.warning("WORKTREE_PREP: Agent dir %s does not exist in worktree", agent_dir)
                venv_ok = False

            # Update work item with worktree info
            if isinstance(current_item, dict):
                current_item["worktree_path"] = str(worktree_path)
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["worktree_branch"] = branch_name
                current_item["context"]["venv_ready"] = venv_ok
            else:
                current_item.worktree_path = str(worktree_path)
                current_item.context["worktree_branch"] = branch_name
                current_item.context["venv_ready"] = venv_ok

            # Also update build_settings with worktree info
            if isinstance(build_settings, dict):
                updated_settings = dict(build_settings)
            else:
                updated_settings = build_settings.model_dump() if hasattr(build_settings, "model_dump") else {}  # type: ignore[attr-defined]
            updated_settings["worktree_path"] = str(worktree_path)
            updated_settings["worktree_branch"] = branch_name

            return {
                "current_work_item": current_item,
                "build_settings": updated_settings,
            }

        except Exception as e:
            logger.error("WORKTREE_PREP: Failed to create worktree: %s", e, exc_info=True)
            if isinstance(current_item, dict):
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["worktree_error"] = str(e)
            else:
                current_item.context["worktree_error"] = str(e)
            return {"current_work_item": current_item}

    return worktree_prep_node
