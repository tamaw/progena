"""
Publish Node - Raises PR from worktree after successful verify.

After the build passes validation/verification, this node:
1. Commits all changes in the worktree
2. Pushes the branch to origin
3. Creates a PR from the worktree branch to main
4. Records the PR URL in the work item

The GitHub Actions CI will then test the PR and on merge,
deploy the agent/mcp to its own Docker container.
"""

import logging
from collections.abc import Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any

from ..runtime.time_utils import utcnow_isoformat
from ..state.models import ProgenitorState
from ..tools.gh import gh_create_pr_from_worktree
from ..tools.git import GitWorktreeManager

logger = logging.getLogger(__name__)


def create_node(
    state_manager,
    worktrees_dir: Path,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the publish node function.

    Args:
        state_manager: State manager for logging decisions.
        worktrees_dir: Path to worktrees directory.
    """
    project_root = worktrees_dir.parent
    git_manager = GitWorktreeManager(worktrees_dir, repo_path=project_root)

    async def publish_node(state: ProgenitorState) -> ProgenitorState:
        """Publish build results by raising a PR.

        Commits changes in the worktree, pushes to remote, and creates a PR.
        Only runs when verification has passed.
        """
        logger.info("PUBLISH: Starting publish node")

        current_item = state.get("current_work_item")
        build_settings = state.get("build_settings") or {}
        verification = state.get("verification", {})

        if not current_item:
            logger.info("PUBLISH: No current work item, skipping")
            return {}

        # Only publish if verification passed
        if not verification.get("current_passed", False):
            logger.info("PUBLISH: Verification not passed, skipping PR")
            return {}

        # Get worktree info
        if isinstance(current_item, dict):
            item_id = current_item.get("id", "unknown")
            worktree_path_str = current_item.get("worktree_path")
            item_type = current_item.get("item_type", "")
            description = current_item.get("description", "")
        else:
            item_id = current_item.id
            worktree_path_str = current_item.worktree_path
            item_type = current_item.item_type
            description = current_item.description

        if not worktree_path_str:
            logger.warning("PUBLISH: No worktree path, skipping PR")
            return {}

        worktree_path = Path(worktree_path_str)
        branch_name = build_settings.get(
            "worktree_branch",
            f"progenitor/build/{item_id}",
        )
        project_name = build_settings.get("project_name", "unknown")

        try:
            # Step 1: Commit changes in worktree
            logger.info("PUBLISH: Committing changes in %s", worktree_path)
            commit_msg = f"feat({project_name}): {description[:80]}\n\nBuilt by Progenitor agent\nWork item: {item_id}"
            commit_result = await git_manager.commit_changes(
                worktree_path=worktree_path,
                message=commit_msg,
            )
            logger.info("PUBLISH: Commit result: %s", commit_result)

            # Step 2: Push and create PR
            pr_title = f"feat({project_name}): {description[:60]}"
            pr_body = _build_pr_body(
                item_id=item_id,
                item_type=item_type,
                project_name=project_name,
                description=description,
                verification=verification,
                build_settings=build_settings,
            )

            logger.info("PUBLISH: Creating PR for %s", branch_name)
            success, pr_url_or_error = await gh_create_pr_from_worktree(
                worktree_path=worktree_path,
                branch=branch_name,
                title=pr_title,
                body=pr_body,
            )

            pr_result = {
                "status": "created" if success else "failed",
                "url": pr_url_or_error if success else None,
                "error": pr_url_or_error if not success else None,
            }

            logger.info("PUBLISH: PR result: %s, URL: %s", pr_result["status"], pr_result.get("url"))

            # Log the PR creation
            await state_manager.log_decision({
                "event": "pr_created",
                "work_item_id": item_id,
                "branch": branch_name,
                "pr_url": pr_result.get("url"),
                "pr_status": pr_result.get("status"),
                "timestamp": utcnow_isoformat(),
            })

            # Update work item with PR info
            if isinstance(current_item, dict):
                current_item["context"] = current_item.get("context", {})
                current_item["context"]["pr_url"] = pr_result.get("url")
                current_item["context"]["pr_status"] = pr_result.get("status")
                current_item["context"]["pr_branch"] = branch_name
            else:
                current_item.context["pr_url"] = pr_result.get("url")
                current_item.context["pr_status"] = pr_result.get("status")
                current_item.context["pr_branch"] = branch_name

            return {
                "current_work_item": current_item,
                "build_messages": state.get("build_messages", []) + [{
                    "phase": "publish",
                    "timestamp": utcnow_isoformat(),
                    "pr_url": pr_result.get("url"),
                    "pr_status": pr_result.get("status"),
                }],
            }

        except Exception as e:
            logger.error("PUBLISH: Failed to create PR: %s", e, exc_info=True)
            await state_manager.log_decision({
                "event": "pr_failed",
                "work_item_id": item_id,
                "error": str(e),
                "timestamp": utcnow_isoformat(),
            })
            return {
                "build_messages": state.get("build_messages", []) + [{
                    "phase": "publish",
                    "timestamp": utcnow_isoformat(),
                    "error": str(e),
                }],
            }

    return publish_node


def _build_pr_body(
    *,
    item_id: str,
    item_type: str,
    project_name: str,
    description: str,
    verification: Mapping[str, Any],
    build_settings: Mapping[str, Any],
) -> str:
    """Build a descriptive PR body."""
    score = verification.get("current_score", 0)
    target = verification.get("target_score", 0.8)

    kind = "Agent" if item_type == "build_agent" else "MCP Server" if item_type == "build_tool" else "Upgrade"
    path = f"app/agents/{project_name}" if item_type == "build_agent" else f"app/mcp/{project_name}"

    return f"""## {kind}: {project_name}

{description}

### Build Details
- **Work Item**: `{item_id}`
- **Type**: {item_type}
- **Path**: `{path}`
- **Verification Score**: {score:.2f} / {target:.2f}

### Automated Build
This PR was created automatically by the Progenitor agent after
the build passed verification. On merge, GitHub Actions will deploy
the {kind.lower()} to its own Docker container and register it with LiteLLM.

### Testing
- Unit tests: Run in worktree
- Integration tests: Use `TEST_MODE=true` environment variable
"""
