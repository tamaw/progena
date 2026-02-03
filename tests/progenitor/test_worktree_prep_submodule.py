"""
Tests for BL-007: Worktree Prep Node with Submodule Support

Tests cover:
1. worktree_prep initializes submodule when build_settings has project_directory
2. worktree_prep skips non-build items
3. worktree_prep handles missing build_settings gracefully
4. worktree_prep handles submodule init errors
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from app.progenitor.nodes.worktree_prep import create_node
from app.progenitor.state.models import WorkItem, WorkItemType, WorkItemStatus


@pytest.fixture
def mock_git_manager():
    """Create a mock GitWorktreeManager."""
    manager = MagicMock()
    manager.init_submodule = AsyncMock(return_value=(True, "Submodule initialized"))
    return manager


@pytest.fixture
def worktree_prep_node(tmp_path, mock_git_manager):
    """Create worktree_prep node with mocked git manager."""
    with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git_manager):
        node = create_node(tmp_path / "worktrees")
        node._git_manager = mock_git_manager  # Expose for testing
        return node, mock_git_manager


class TestWorktreePrepWithBuildSettings:
    """Test worktree_prep with build_settings."""

    @pytest.mark.asyncio
    async def test_initializes_submodule_with_build_settings(self, tmp_path):
        """Test that submodule is initialized when build_settings has project_directory."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock(return_value=(True, "Submodule initialized at /path"))

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "build_agent",
                    "context": {},
                },
                "build_settings": {
                    "project_name": "weather_agent",
                    "project_directory": "app/agents/weather_agent",
                },
            }

            result = await node(state)

            # Should have called init_submodule
            mock_git.init_submodule.assert_called_once()
            call_args = mock_git.init_submodule.call_args

            assert call_args.kwargs["submodule_name"] == "weather_agent"
            # Check work item was updated (normalize path separators for cross-platform)
            submodule_path = result["current_work_item"]["submodule_path"].replace("\\", "/")
            assert submodule_path == "app/agents/weather_agent"
            assert result["current_work_item"]["context"]["submodule_initialized"] is True

    @pytest.mark.asyncio
    async def test_skips_non_build_items(self, tmp_path):
        """Test that non-build items skip submodule initialization."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock()

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "direct_answer",  # Not a build type
                    "context": {},
                },
            }

            result = await node(state)

            # Should NOT have called init_submodule
            mock_git.init_submodule.assert_not_called()
            # Should return empty (no updates)
            assert result == {}

    @pytest.mark.asyncio
    async def test_handles_missing_build_settings(self, tmp_path):
        """Test graceful handling when build_settings is missing."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock()

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "build_agent",
                    "context": {},
                },
                # No build_settings
            }

            result = await node(state)

            # Should NOT have called init_submodule (no project_directory)
            mock_git.init_submodule.assert_not_called()
            # Should mark as skipped
            assert result["current_work_item"]["context"]["submodule_skipped"] is True

    @pytest.mark.asyncio
    async def test_handles_missing_project_directory(self, tmp_path):
        """Test graceful handling when project_directory is missing."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock()

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "build_agent",
                    "context": {},
                },
                "build_settings": {
                    "project_name": "test",
                    # No project_directory
                },
            }

            result = await node(state)

            # Should NOT have called init_submodule
            mock_git.init_submodule.assert_not_called()
            assert result["current_work_item"]["context"]["submodule_skipped"] is True


class TestWorktreePrepErrorHandling:
    """Test error handling in worktree_prep."""

    @pytest.mark.asyncio
    async def test_handles_submodule_init_failure(self, tmp_path):
        """Test handling when submodule initialization fails."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock(return_value=(False, "Git init failed"))

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "build_agent",
                    "context": {},
                },
                "build_settings": {
                    "project_name": "test_agent",
                    "project_directory": "app/agents/test_agent",
                },
            }

            result = await node(state)

            # Should have attempted init_submodule
            mock_git.init_submodule.assert_called_once()
            # Should record failure
            assert result["current_work_item"]["context"]["submodule_initialized"] is False

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, tmp_path):
        """Test that exceptions don't crash the node."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock(side_effect=Exception("Unexpected error"))

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": "build_agent",
                    "context": {},
                },
                "build_settings": {
                    "project_name": "test_agent",
                    "project_directory": "app/agents/test_agent",
                },
            }

            # Should not raise
            result = await node(state)

            # Should record error
            assert "submodule_error" in result["current_work_item"]["context"]


class TestWorktreePrepBuildTypes:
    """Test different build types trigger submodule init."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("item_type", ["build_agent", "build_mcp", "upgrade"])
    async def test_all_build_types_trigger_submodule(self, tmp_path, item_type):
        """Test that all build types trigger submodule initialization."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock(return_value=(True, "OK"))

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": item_type,
                    "context": {},
                },
                "build_settings": {
                    "project_name": "test",
                    "project_directory": "app/agents/test",
                },
            }

            result = await node(state)

            mock_git.init_submodule.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("item_type", ["direct_answer", "a2a_call", "mcp_call", "system_tool"])
    async def test_non_build_types_skip_submodule(self, tmp_path, item_type):
        """Test that non-build types skip submodule initialization."""
        mock_git = MagicMock()
        mock_git.init_submodule = AsyncMock()

        with patch('app.progenitor.nodes.worktree_prep.GitWorktreeManager', return_value=mock_git):
            node = create_node(tmp_path / "worktrees")

            state = {
                "current_work_item": {
                    "id": "work_123",
                    "item_type": item_type,
                    "context": {},
                },
            }

            result = await node(state)

            mock_git.init_submodule.assert_not_called()
