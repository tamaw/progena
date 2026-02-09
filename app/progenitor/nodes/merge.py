"""
MERGE Node - Sequentially merges concurrent builds to main branch.

Handles:
- Sequential merging of worktrees to avoid conflicts
- LLM-based conflict resolution when merges fail
- Retry logic for transient merge failures
- Cleanup of merged worktrees
"""

import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.time_utils import utcnow, utcnow_isoformat
from ..state.models import ProgenitorState, WorkItemStatus

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    git_manager,
    state_manager,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the MERGE node that sequentially merges worktrees.

    Args:
        llm_client: LLM client for conflict resolution.
        git_manager: GitWorktreeManager for git operations.
        state_manager: State manager for saving state.

    Returns:
        Async node function for LangGraph.
    """

    async def merge_node(state: ProgenitorState) -> ProgenitorState:
        """MERGE phase - sequentially merge concurrent builds to main.

        For each worktree in merge_queue:
        1. Try to merge to main
        2. If conflict, use LLM to resolve
        3. Mark work item completed/failed
        4. Clean up worktree
        """
        logger.info("MERGE: Starting merge phase")

        merge_queue = list(state.get("merge_queue", []))

        if not merge_queue:
            logger.info("MERGE: No builds to merge")
            return {}

        logger.info(f"MERGE: Merging {len(merge_queue)} builds sequentially")

        backlog = list(state.get("backlog", []))
        merged_items = []
        failed_items = []

        # Sequential merge to avoid conflicts
        for queue_item in merge_queue:
            work_item_id = queue_item["work_item_id"]
            worktree_path = Path(queue_item["worktree_path"])

            logger.info(f"MERGE: Attempting merge for {work_item_id}")

            try:
                # Attempt merge
                merge_success = await git_manager.merge_to_main(
                    worktree_path=worktree_path,
                    target_branch="main",
                )

                if merge_success:
                    queue_item["merge_status"] = "merged"
                    queue_item["merge_commit_hash"] = await _get_commit_hash(
                        git_manager, git_manager.repo_path
                    )
                    merged_items.append(work_item_id)

                    # Mark work item as completed
                    _mark_item_completed(backlog, work_item_id)

                    logger.info(f"MERGE: Successfully merged {work_item_id}")

                else:
                    # Merge conflict - attempt LLM resolution
                    logger.warning(f"MERGE: Conflict detected for {work_item_id}")

                    conflict_resolved = await _resolve_conflict_with_llm(
                        llm_client=llm_client,
                        git_manager=git_manager,
                        worktree_path=worktree_path,
                        work_item_id=work_item_id,
                        state=state,
                    )

                    if conflict_resolved:
                        queue_item["merge_status"] = "resolved"
                        merged_items.append(work_item_id)
                        _mark_item_completed(backlog, work_item_id)
                        logger.info(f"MERGE: Conflict resolved and merged for {work_item_id}")
                    else:
                        queue_item["merge_status"] = "failed"
                        queue_item["merge_attempt_count"] += 1
                        failed_items.append(work_item_id)

                        # Mark work item as failed
                        _mark_item_failed(backlog, work_item_id, "Merge conflict could not be resolved")
                        logger.error(f"MERGE: Failed to resolve conflict for {work_item_id}")

                # Clean up worktree
                await git_manager.remove_worktree(worktree_path)
                logger.info(f"MERGE: Cleaned up worktree for {work_item_id}")

            except Exception as e:
                logger.error(f"MERGE: Exception during merge for {work_item_id}: {e}", exc_info=True)
                queue_item["merge_status"] = "failed"
                queue_item["error_message"] = str(e)
                failed_items.append(work_item_id)

                # Mark work item as failed
                _mark_item_failed(backlog, work_item_id, str(e))

                # Clean up failed worktree
                try:
                    await git_manager.cleanup_failed_worktree(worktree_path)
                except Exception as cleanup_error:
                    logger.warning(f"MERGE: Failed to cleanup worktree for {work_item_id}: {cleanup_error}")

        # Save updated state
        await state_manager.save_build_state({
            **state,
            "merge_queue": merge_queue,
            "backlog": backlog,
        })

        logger.info(
            f"MERGE: Complete. Merged: {len(merged_items)}, Failed: {len(failed_items)}"
        )

        return {
            "merge_queue": merge_queue,
            "backlog": backlog,
            "merged_items": merged_items,
            "failed_merge_items": failed_items,
        }

    return merge_node


async def _resolve_conflict_with_llm(
    llm_client,
    git_manager,
    worktree_path: Path,
    work_item_id: str,
    state: ProgenitorState,
) -> bool:
    """Resolve merge conflicts using LLM.

    Strategy:
    1. Get list of conflicted files from git
    2. Read conflict markers from files
    3. Ask LLM to resolve based on:
       - Build context from work item
       - Diff from branch
       - Current main state
    4. Apply LLM's resolution
    5. Commit and retry merge

    Args:
        llm_client: LLM client for structured output.
        git_manager: GitWorktreeManager for git operations.
        worktree_path: Path to the worktree with conflicts.
        work_item_id: ID of the work item being merged.
        state: Current progenitor state.

    Returns:
        True if resolved and merged successfully.
    """
    logger.info(f"MERGE: Resolving conflicts for {work_item_id} using LLM")

    # 1. Get conflicted files
    conflict_files = await _get_conflicted_files(git_manager, worktree_path)

    if not conflict_files:
        logger.warning("MERGE: No conflict files found, but merge failed")
        return False

    logger.info(f"MERGE: Found {len(conflict_files)} conflicted files: {conflict_files}")

    # 2. Read conflict content
    conflicts_data = []
    for file_path in conflict_files:
        file_full_path = worktree_path / file_path
        try:
            with open(file_full_path, encoding='utf-8') as f:
                content = f.read()
            conflicts_data.append({
                "file_path": file_path,
                "content": content,
            })
        except Exception as e:
            logger.error(f"MERGE: Failed to read {file_path}: {e}")
            return False

    # 3. Build LLM prompt for conflict resolution
    prompt = _build_conflict_resolution_prompt(
        conflicts_data=conflicts_data,
        work_item_id=work_item_id,
        state=state,
    )

    # 4. Call LLM
    try:
        # Get response format for structured output
        response_format = llm_client._get_json_response_format(
            ConflictResolution, "conflict_resolution"
        )

        system_content = "You are a git merge conflict resolver."
        if response_format.get("type") == "json_object":
            system_content += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_content,
            temperature=0.2,
            response_format=response_format,
        )

        # Parse and validate response
        raw_data = json.loads(response)
        resolution_response = ConflictResolution.model_validate(raw_data)

        # 5. Apply resolution
        for file_resolution in resolution_response.file_resolutions:
            file_path = worktree_path / file_resolution.file_path
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(file_resolution.resolved_content)
                logger.info(f"MERGE: Applied resolution to {file_resolution.file_path}")
            except Exception as e:
                logger.error(f"MERGE: Failed to write resolution to {file_resolution.file_path}: {e}")
                return False

        # 6. Stage resolved files
        await git_manager._run_git(["add", *conflict_files], cwd=worktree_path)

        # 7. Commit resolution
        commit_hash = await git_manager.commit_changes(
            worktree_path=worktree_path,
            message=f"Resolve merge conflicts for {work_item_id}\n\nAI-assisted conflict resolution",
        )

        if not commit_hash:
            logger.error("MERGE: Failed to commit conflict resolution")
            return False

        logger.info(f"MERGE: Committed resolution: {commit_hash}")

        # 8. Retry merge
        merge_success = await git_manager.merge_to_main(
            worktree_path=worktree_path,
            target_branch="main",
        )

        return merge_success

    except Exception as e:
        logger.error(f"MERGE: LLM conflict resolution failed: {e}", exc_info=True)
        return False


def _build_conflict_resolution_prompt(
    conflicts_data: list[dict],
    work_item_id: str,
    state: ProgenitorState,
) -> str:
    """Build prompt for LLM to resolve conflicts.

    Args:
        conflicts_data: List of dicts with file_path and content.
        work_item_id: ID of the work item.
        state: Current progenitor state.

    Returns:
        Prompt string for LLM.
    """
    prompt_parts = [
        f"# Merge Conflict Resolution for Work Item: {work_item_id}",
        "",
        "## Context",
        "Multiple concurrent builds were performed, and this build's changes conflict with main.",
        "",
        "## Your Task",
        "Resolve the git merge conflicts in the following files. For each file:",
        "1. Analyze the conflict markers (<<<<<<< HEAD, =======, >>>>>>>)",
        "2. Understand what each side is trying to do",
        "3. Produce a resolved version that integrates both changes intelligently",
        "",
        "IMPORTANT: Remove all conflict markers from your resolved content.",
        "",
        "## Conflicted Files",
        "",
    ]

    for conflict in conflicts_data:
        prompt_parts.extend([
            f"### File: {conflict['file_path']}",
            "```",
            conflict['content'],
            "```",
            "",
        ])

    prompt_parts.extend([
        "## Instructions",
        "For each file, provide:",
        "1. file_path: The path to the file",
        "2. resolved_content: The fully resolved content (no conflict markers)",
        "3. reasoning: Brief explanation of your resolution strategy",
        "",
        "Respond with a JSON object following this schema:",
        "```json",
        "{",
        '  "file_resolutions": [',
        '    {',
        '      "file_path": "path/to/file",',
        '      "resolved_content": "full resolved content without conflict markers",',
        '      "reasoning": "why you resolved it this way"',
        '    }',
        '  ]',
        "}",
        "```",
    ])

    return "\n".join(prompt_parts)


async def _get_conflicted_files(
    git_manager,
    worktree_path: Path,
) -> list[str]:
    """Get list of files with merge conflicts.

    Args:
        git_manager: GitWorktreeManager instance.
        worktree_path: Path to worktree.

    Returns:
        List of file paths with conflicts.
    """
    result = await git_manager._run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=worktree_path
    )

    if result["returncode"] != 0:
        return []

    files = [f.strip() for f in result["stdout"].split("\n") if f.strip()]
    return files


async def _get_commit_hash(
    git_manager,
    repo_path: Path,
) -> str:
    """Get the current commit hash.

    Args:
        git_manager: GitWorktreeManager instance.
        repo_path: Path to repository.

    Returns:
        Commit hash string.
    """
    result = await git_manager._run_git(
        ["rev-parse", "HEAD"],
        cwd=repo_path
    )
    return result["stdout"].strip()


def _mark_item_completed(backlog: list, work_item_id: str) -> None:
    """Mark work item as completed in backlog.

    Args:
        backlog: List of work items.
        work_item_id: ID of item to mark completed.
    """
    for item in backlog:
        item_id = item.id if hasattr(item, "id") else item.get("id")
        if item_id == work_item_id:
            if hasattr(item, "status"):
                item.status = WorkItemStatus.COMPLETED
                item.updated_at = utcnow()
            else:
                item["status"] = "completed"
                item["updated_at"] = utcnow_isoformat()
            break


def _mark_item_failed(backlog: list, work_item_id: str, error: str) -> None:
    """Mark work item as failed in backlog.

    Args:
        backlog: List of work items.
        work_item_id: ID of item to mark failed.
        error: Error message.
    """
    for item in backlog:
        item_id = item.id if hasattr(item, "id") else item.get("id")
        if item_id == work_item_id:
            if hasattr(item, "status"):
                item.status = WorkItemStatus.FAILED
                item.error_message = error
                item.updated_at = utcnow()
            else:
                item["status"] = "failed"
                item["error_message"] = error
                item["updated_at"] = utcnow_isoformat()
            break


# Pydantic models for LLM structured output

class FileResolution(BaseModel):
    """Resolution for a single conflicted file."""
    file_path: str = Field(description="Path to the file")
    resolved_content: str = Field(description="Fully resolved content without conflict markers")
    reasoning: str = Field(description="Explanation of resolution strategy")


class ConflictResolution(BaseModel):
    """LLM response for conflict resolution."""
    file_resolutions: list[FileResolution] = Field(description="Resolutions for each file")
