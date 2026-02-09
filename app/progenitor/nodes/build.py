"""
Build Node - Produces candidate outputs via concurrent Copilot CLI runs.

This is the core build step that generates code artifacts in the inner loop.
Extracted from graph_inner_loop.py for consistency with other node patterns.

The build node picks pending backlog items and processes them concurrently,
marking each as in_progress during the build. Items are marked completed after
successful merge to main (handled by MERGE node).
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..runtime.time_utils import utcnow, utcnow_isoformat
from ..state.helpers import get_actionable_items
from ..state.models import ProgenitorState, WorkItemStatus

if TYPE_CHECKING:
    from ..dependencies import NodeDependencies
    from ..tools.copilot import CopilotCLI
    from ..tools.git import GitWorktreeManager

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    copilot_cli: "CopilotCLI",
    worktrees_dir: Path,
    state_manager,
    git_manager: "GitWorktreeManager",
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the BUILD node that runs Copilot CLI concurrently.

    This is the core build step that generates code artifacts.

    Args:
        llm_client: LLM client for model calls (currently unused but for future).
        copilot_cli: CopilotCLI instance for running builds.
        worktrees_dir: Path to worktrees directory.
        state_manager: State manager for saving build state.
        git_manager: GitWorktreeManager for creating/managing worktrees.

    Returns:
        Async node function for LangGraph.
    """

    async def build_node(state: ProgenitorState) -> ProgenitorState:
        """BUILD phase - produce candidate outputs via concurrent Copilot CLI runs.

        Picks N pending backlog items (where N = min(pending_count, max_concurrency))
        and runs copilot concurrently for each in its own worktree.
        """
        logger.info("BUILD: Starting concurrent build phase")

        # Get configuration
        max_concurrency = state.get("max_concurrency", 3)
        backlog = list(state.get("backlog", []))
        pending_items = get_actionable_items(backlog)

        # Determine how many to process
        num_to_process = min(len(pending_items), max_concurrency)
        items_to_process = pending_items[:num_to_process]

        if not items_to_process:
            logger.info("BUILD: No pending items")
            return cast(ProgenitorState, {})

        logger.info(f"BUILD: Processing {num_to_process} items concurrently (max={max_concurrency})")

        # Create worktrees for each item
        concurrent_builds = []
        for item in items_to_process:
            item_id = item.id if hasattr(item, "id") else item["id"]

            try:
                # Create worktree
                worktree_path = await git_manager.create_worktree(
                    work_item_id=item_id,
                    branch_name=f"progenitor/build/{item_id}"
                )

                concurrent_builds.append({
                    "work_item_id": item_id,
                    "worktree_path": str(worktree_path),
                    "branch_name": f"progenitor/build/{item_id}",
                    "status": "building",
                    "started_at": utcnow_isoformat(),
                    "build_result": None,
                })
                logger.info(f"BUILD: Created worktree for {item_id} at {worktree_path}")
            except Exception as e:
                logger.error(f"BUILD: Failed to create worktree for {item_id}: {e}", exc_info=True)
                concurrent_builds.append({
                    "work_item_id": item_id,
                    "worktree_path": "",
                    "branch_name": f"progenitor/build/{item_id}",
                    "status": "failed",
                    "started_at": utcnow_isoformat(),
                    "error_message": f"Failed to create worktree: {e}",
                })

        # Launch concurrent builds
        build_tasks = [
            _run_single_build(
                copilot_cli=copilot_cli,
                work_item=item,
                worktree_path=Path(build_info["worktree_path"]),
                state=state,
            )
            for item, build_info in zip(items_to_process, concurrent_builds, strict=True)
            if build_info["status"] == "building"  # Only run builds that have valid worktrees
        ]

        logger.info(f"BUILD: Launching {len(build_tasks)} concurrent builds")

        # Wait for all builds to complete
        build_results = await asyncio.gather(*build_tasks, return_exceptions=True)

        # Update concurrent_builds with results
        result_idx = 0
        for build_info in concurrent_builds:
            if build_info["status"] == "building":
                result = build_results[result_idx]
                result_idx += 1

                if isinstance(result, Exception):
                    build_info["status"] = "failed"
                    build_info["error_message"] = str(result)
                    build_info["completed_at"] = utcnow_isoformat()
                    logger.error(f"BUILD: Build {build_info['work_item_id']} failed with exception: {result}")
                else:
                    # Check if build was successful
                    if result.get("success", False):
                        build_info["status"] = "completed"
                    else:
                        build_info["status"] = "failed"
                        build_info["error_message"] = result.get("error", "Build failed")
                    build_info["build_result"] = result
                    build_info["completed_at"] = utcnow_isoformat()
                    logger.info(f"BUILD: Build {build_info['work_item_id']} completed, success={result.get('success')}")

        # Mark items as in_progress (not completed yet - need merge first)
        for item in items_to_process:
            if hasattr(item, "status"):
                item.status = WorkItemStatus.IN_PROGRESS
                item.updated_at = utcnow()
            else:
                item["status"] = "in_progress"
                item["updated_at"] = utcnow().isoformat()

        # Create merge queue from successful builds
        merge_queue = [
            {
                "work_item_id": build["work_item_id"],
                "worktree_path": build["worktree_path"],
                "branch_name": build["branch_name"],
                "merge_status": "pending",
                "merge_attempt_count": 0,
                "conflict_files": [],
            }
            for build in concurrent_builds
            if build["status"] == "completed"  # Only completed (successful) builds
        ]

        logger.info(f"BUILD: {len(merge_queue)} builds ready for merge, {sum(1 for b in concurrent_builds if b['status'] == 'failed')} failed")

        # Track iteration
        iteration_state = state.get("iteration", {})
        current_iteration = iteration_state.get("current", 0) if isinstance(iteration_state, dict) else getattr(iteration_state, "current", 0)

        # Update build state
        result: dict[str, Any] = {
            "build_phase": "build",
            "concurrent_builds": concurrent_builds,
            "merge_queue": merge_queue,
            "backlog": backlog,
            "build_messages": state.get("build_messages", []) + [
                {
                    "phase": "build",
                    "iteration": current_iteration,
                    "timestamp": utcnow_isoformat(),
                    "concurrent_count": len(concurrent_builds),
                    "success_count": len(merge_queue),
                    "failed_count": sum(1 for b in concurrent_builds if b["status"] == "failed"),
                }
            ],
        }

        # Save checkpoint to build_state.json
        await state_manager.save_build_state({
            **state,
            **result,
        })

        return cast(ProgenitorState, result)

    return build_node


async def _run_single_build(
    copilot_cli: "CopilotCLI",
    work_item: Any,
    worktree_path: Path,
    state: ProgenitorState,
) -> dict[str, Any]:
    """Run a single build in a worktree.

    Args:
        copilot_cli: CopilotCLI instance for running builds.
        work_item: Work item to build.
        worktree_path: Path to the worktree for this build.
        state: Current progenitor state.

    Returns:
        Build result dict from Copilot CLI.
    """
    item_id = work_item.id if hasattr(work_item, "id") else work_item["id"]

    logger.info(f"BUILD: Starting build for {item_id} in {worktree_path}")

    # Get copilot_prompt from work item context
    if hasattr(work_item, "context"):
        context = work_item.context if isinstance(work_item.context, dict) else {}
    else:
        context = work_item.get("context", {})

    copilot_prompt = context.get("copilot_prompt")

    if not copilot_prompt:
        # Fallback prompt
        task_description = state.get("build_task_description", f"Build item {item_id}")
        copilot_prompt = f"Build according to spec. Task: {task_description}"
        logger.warning(f"BUILD: No copilot_prompt for {item_id}, using fallback")

    # Run Copilot CLI
    try:
        build_result = await copilot_cli.build(
            work_item_id=item_id,
            worktree_path=worktree_path,
            prompt=copilot_prompt,
        )

        logger.info(f"BUILD: Completed build for {item_id}, success={build_result.get('success')}")
        return build_result

    except Exception as e:
        logger.error(f"BUILD: Exception during build for {item_id}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "work_item_id": item_id,
        }


def create_node_from_deps(deps: "NodeDependencies") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create build node from NodeDependencies container.

    This is the preferred factory method using the new dependency injection pattern.

    Args:
        deps: NodeDependencies container with all required dependencies.

    Returns:
        Async node function for LangGraph.
    """
    from ..tools.copilot import CopilotCLI
    from ..tools.git import GitWorktreeManager

    config = deps.require_config()
    worktrees_dir = deps.require_worktrees_dir()

    copilot_cli = CopilotCLI(
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        default_flags=config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        timeout=config.copilot.timeout_seconds,
    )

    git_manager = GitWorktreeManager(worktrees_dir)

    return create_node(
        llm_client=deps.llm_client,
        copilot_cli=copilot_cli,
        worktrees_dir=worktrees_dir,
        state_manager=deps.require_state_manager(),
        git_manager=git_manager,
    )
