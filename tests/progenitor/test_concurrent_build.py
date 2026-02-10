"""Tests for concurrent build functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any, cast
from pathlib import Path
from datetime import datetime, timezone

from app.progenitor.state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemType,
    WorkItemStatus,
)


class TestConcurrentBuildNode:
    """Tests for the concurrent BUILD node."""

    @pytest.fixture
    def mock_git_manager(self):
        """Create a mock GitWorktreeManager."""
        git_manager = AsyncMock()
        git_manager.create_worktree = AsyncMock(side_effect=lambda work_item_id, branch_name: Path(f"/worktrees/{work_item_id}"))
        git_manager.remove_worktree = AsyncMock()
        git_manager.cleanup_failed_worktree = AsyncMock()
        return git_manager

    @pytest.fixture
    def mock_copilot_cli(self):
        """Create a mock CopilotCLI."""
        copilot_cli = AsyncMock()
        copilot_cli.build = AsyncMock(return_value={"success": True, "message": "Build completed"})
        return copilot_cli

    @pytest.fixture
    def mock_state_manager(self):
        """Create a mock StateManager."""
        state_manager = AsyncMock()
        state_manager.save_build_state = AsyncMock()
        return state_manager

    @pytest.fixture
    def sample_backlog(self):
        """Create sample backlog items."""
        return [
            WorkItem(
                id="item-1",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description="Build agent 1",
                context={"copilot_prompt": "Build agent 1"},
                created_at=datetime.now(timezone.utc),
            ),
            WorkItem(
                id="item-2",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description="Build agent 2",
                context={"copilot_prompt": "Build agent 2"},
                created_at=datetime.now(timezone.utc),
            ),
            WorkItem(
                id="item-3",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description="Build agent 3",
                context={"copilot_prompt": "Build agent 3"},
                created_at=datetime.now(timezone.utc),
            ),
        ]

    @pytest.mark.asyncio
    async def test_adaptive_concurrency_single_item(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that only 1 build runs when backlog has 1 item."""
        from app.progenitor.nodes import build

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": [sample_backlog[0]],  # Only 1 item
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should only create 1 worktree
        assert mock_git_manager.create_worktree.call_count == 1

        # Should have 1 concurrent build
        assert len(result["concurrent_builds"]) == 1
        assert result["concurrent_builds"][0]["work_item_id"] == "item-1"

    @pytest.mark.asyncio
    async def test_concurrent_build_3_items(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that 3 builds run concurrently when backlog has 3 items."""
        from app.progenitor.nodes import build

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should create 3 worktrees
        assert mock_git_manager.create_worktree.call_count == 3

        # Should have 3 concurrent builds
        assert len(result["concurrent_builds"]) == 3

        # All should be completed
        for build_info in result["concurrent_builds"]:
            assert build_info["status"] == "completed"

    @pytest.mark.asyncio
    async def test_max_concurrency_limit(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that max_concurrency limit is respected."""
        from app.progenitor.nodes import build

        # Add 2 more items to have 5 total
        sample_backlog.extend([
            WorkItem(
                id="item-4",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description="Build agent 4",
                context={"copilot_prompt": "Build agent 4"},
                created_at=datetime.now(timezone.utc),
            ),
            WorkItem(
                id="item-5",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description="Build agent 5",
                context={"copilot_prompt": "Build agent 5"},
                created_at=datetime.now(timezone.utc),
            ),
        ])

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 2,  # Limit to 2
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should only create 2 worktrees (respecting max_concurrency)
        assert mock_git_manager.create_worktree.call_count == 2

        # Should have 2 concurrent builds
        assert len(result["concurrent_builds"]) == 2

    @pytest.mark.asyncio
    async def test_merge_queue_created_from_successful_builds(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that merge queue is created from successful builds."""
        from app.progenitor.nodes import build

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should have merge queue with all successful builds
        assert len(result["merge_queue"]) == 3

        # All should be pending merge
        for queue_item in result["merge_queue"]:
            assert queue_item["merge_status"] == "pending"
            assert queue_item["merge_attempt_count"] == 0

    @pytest.mark.asyncio
    async def test_build_failure_not_in_merge_queue(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that failed builds are not added to merge queue."""
        from app.progenitor.nodes import build

        # Make one build fail
        async def build_side_effect(work_item_id, worktree_path, prompt):
            if work_item_id == "item-2":
                raise Exception("Build failed")
            return {"success": True, "message": "Build completed"}

        mock_copilot_cli.build = AsyncMock(side_effect=build_side_effect)

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should have 3 concurrent builds
        assert len(result["concurrent_builds"]) == 3

        # But only 2 in merge queue (item-2 failed)
        assert len(result["merge_queue"]) == 2

        # Failed build should be marked as failed
        failed_builds = [b for b in result["concurrent_builds"] if b["status"] == "failed"]
        assert len(failed_builds) == 1
        assert failed_builds[0]["work_item_id"] == "item-2"

    @pytest.mark.asyncio
    async def test_worktree_creation_failure(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test handling of worktree creation failure."""
        from app.progenitor.nodes import build

        # Make one worktree creation fail
        async def create_worktree_side_effect(work_item_id, branch_name):
            if work_item_id == "item-1":
                raise Exception("Worktree creation failed")
            return Path(f"/worktrees/{work_item_id}")

        mock_git_manager.create_worktree = AsyncMock(side_effect=create_worktree_side_effect)

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # Should have 3 concurrent builds tracked
        assert len(result["concurrent_builds"]) == 3

        # Item-1 should be marked as failed
        item1_build = [b for b in result["concurrent_builds"] if b["work_item_id"] == "item-1"][0]
        assert item1_build["status"] == "failed"
        assert "Failed to create worktree" in item1_build["error_message"]

        # Only 2 should be in merge queue
        assert len(result["merge_queue"]) == 2

    @pytest.mark.asyncio
    async def test_work_items_marked_in_progress(self, mock_copilot_cli, mock_state_manager, mock_git_manager, sample_backlog):
        """Test that work items are marked as IN_PROGRESS during build."""
        from app.progenitor.nodes import build

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": sample_backlog,
            "build_messages": [],
            "iteration": {"current": 1},
        }

        result = cast(dict[str, Any], await node(state))

        # All processed items should be marked IN_PROGRESS
        backlog = result["backlog"]
        for item in backlog:
            assert item.status == WorkItemStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_no_pending_items_returns_empty(self, mock_copilot_cli, mock_state_manager, mock_git_manager):
        """Test that node returns empty when no pending items."""
        from app.progenitor.nodes import build

        node = build.create_node(
            llm_client=MagicMock(),
            copilot_cli=mock_copilot_cli,
            worktrees_dir=Path("/worktrees"),
            state_manager=mock_state_manager,
            git_manager=mock_git_manager,
        )

        state: ProgenitorState = {
            "max_concurrency": 3,
            "backlog": [],  # Empty backlog
            "build_messages": [],
        }

        result = cast(dict[str, Any], await node(state))

        # Should not create any worktrees
        assert mock_git_manager.create_worktree.call_count == 0

        # Result should be empty
        assert result == {}


class TestMergeNode:
    """Tests for the MERGE node."""

    @pytest.fixture
    def mock_git_manager(self):
        """Create a mock GitWorktreeManager."""
        git_manager = AsyncMock()
        git_manager.merge_to_main = AsyncMock(return_value=True)
        git_manager.remove_worktree = AsyncMock()
        git_manager.cleanup_failed_worktree = AsyncMock()
        git_manager.repo_path = Path("/repo")
        git_manager._run_git = AsyncMock(return_value={"returncode": 0, "stdout": "abc123", "stderr": ""})
        return git_manager

    @pytest.fixture
    def mock_state_manager(self):
        """Create a mock StateManager."""
        state_manager = AsyncMock()
        state_manager.save_build_state = AsyncMock()
        return state_manager

    @pytest.fixture
    def sample_merge_queue(self):
        """Create sample merge queue."""
        return [
            {
                "work_item_id": "item-1",
                "worktree_path": "/worktrees/item-1",
                "branch_name": "progenitor/build/item-1",
                "merge_status": "pending",
                "merge_attempt_count": 0,
                "conflict_files": [],
            },
            {
                "work_item_id": "item-2",
                "worktree_path": "/worktrees/item-2",
                "branch_name": "progenitor/build/item-2",
                "merge_status": "pending",
                "merge_attempt_count": 0,
                "conflict_files": [],
            },
        ]

    @pytest.fixture
    def sample_backlog(self):
        """Create sample backlog items."""
        return [
            WorkItem(
                id="item-1",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.IN_PROGRESS,
                description="Build agent 1",
                created_at=datetime.now(timezone.utc),
            ),
            WorkItem(
                id="item-2",
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.IN_PROGRESS,
                description="Build agent 2",
                created_at=datetime.now(timezone.utc),
            ),
        ]

    @pytest.mark.asyncio
    async def test_sequential_merge_success(self, mock_git_manager, mock_state_manager, sample_merge_queue, sample_backlog):
        """Test successful sequential merging of all items."""
        from app.progenitor.nodes import merge

        node = merge.create_node(
            llm_client=MagicMock(),
            git_manager=mock_git_manager,
            state_manager=mock_state_manager,
        )

        state: ProgenitorState = {
            "merge_queue": sample_merge_queue,
            "backlog": sample_backlog,
        }

        result = cast(dict[str, Any], await node(state))

        # Should attempt merge for all items
        assert mock_git_manager.merge_to_main.call_count == 2

        # All should be marked as merged
        for queue_item in result["merge_queue"]:
            assert queue_item["merge_status"] == "merged"

        # All should be in merged_items
        assert len(result["merged_items"]) == 2

        # Should clean up worktrees
        assert mock_git_manager.remove_worktree.call_count == 2

        # Backlog items should be marked completed
        for item in result["backlog"]:
            assert item.status == WorkItemStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_merge_conflict_without_resolution(self, mock_git_manager, mock_state_manager, sample_merge_queue, sample_backlog):
        """Test merge failure when conflict cannot be resolved."""
        from app.progenitor.nodes import merge

        # Make merge fail (conflict)
        mock_git_manager.merge_to_main = AsyncMock(return_value=False)

        # Make conflict resolution fail (return empty conflict files)
        mock_git_manager._run_git = AsyncMock(return_value={"returncode": 0, "stdout": "", "stderr": ""})

        node = merge.create_node(
            llm_client=MagicMock(),
            git_manager=mock_git_manager,
            state_manager=mock_state_manager,
        )

        state: ProgenitorState = {
            "merge_queue": sample_merge_queue[:1],  # Only test 1 item
            "backlog": sample_backlog[:1],
        }

        result = cast(dict[str, Any], await node(state))

        # Should be marked as failed
        assert result["merge_queue"][0]["merge_status"] == "failed"

        # Should be in failed_items
        assert len(result["failed_merge_items"]) == 1

        # Backlog item should be marked failed
        assert result["backlog"][0].status == WorkItemStatus.FAILED

    @pytest.mark.asyncio
    async def test_empty_merge_queue_returns_empty(self, mock_git_manager, mock_state_manager):
        """Test that node returns empty when no items to merge."""
        from app.progenitor.nodes import merge

        node = merge.create_node(
            llm_client=MagicMock(),
            git_manager=mock_git_manager,
            state_manager=mock_state_manager,
        )

        state: ProgenitorState = {
            "merge_queue": [],
            "backlog": [],
        }

        result = cast(dict[str, Any], await node(state))

        # Should not attempt any merges
        assert mock_git_manager.merge_to_main.call_count == 0

        # Result should be empty
        assert result == {}

    @pytest.mark.asyncio
    async def test_merge_exception_handling(self, mock_git_manager, mock_state_manager, sample_merge_queue, sample_backlog):
        """Test handling of exceptions during merge."""
        from app.progenitor.nodes import merge

        # Make merge raise exception
        mock_git_manager.merge_to_main = AsyncMock(side_effect=Exception("Git merge error"))

        node = merge.create_node(
            llm_client=MagicMock(),
            git_manager=mock_git_manager,
            state_manager=mock_state_manager,
        )

        state: ProgenitorState = {
            "merge_queue": sample_merge_queue[:1],
            "backlog": sample_backlog[:1],
        }

        result = await node(state)

        # Should be marked as failed
        assert result["merge_queue"][0]["merge_status"] == "failed"

        # Should have error message
        assert "error_message" in result["merge_queue"][0]
        assert "Git merge error" in result["merge_queue"][0]["error_message"]

        # Should attempt cleanup of failed worktree
        assert mock_git_manager.cleanup_failed_worktree.call_count == 1
