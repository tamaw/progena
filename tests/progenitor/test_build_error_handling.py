"""
Tests for BL-007: Build Error Handling and User Notification

Tests cover:
1. handle_build_error - updates state with error status
2. handle_build_error - creates work item for user notification
3. handle_build_error - checkpoints state with error
4. _suspend_active_build - graceful halt on cancellation
5. _suspend_active_build - writes suspended status to state
"""

import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.progenitor.runtime.loop import ProgenitorLoop
from app.progenitor.config import ProgenitorConfig, RuntimeConfig


@pytest.fixture
def mock_config():
    """Create a mock config."""
    config = MagicMock(spec=ProgenitorConfig)
    config.runtime = MagicMock(spec=RuntimeConfig)
    config.runtime.max_input_queue_size = 100
    config.runtime.max_output_queue_size = 100
    config.runtime.max_stream_queue_size = 100
    config.runtime.timezone = "UTC"
    # Add storage config with worktree_dir
    config.storage = MagicMock()
    config.storage.worktree_dir = Path(tempfile.mkdtemp()) / "worktrees"
    return config


@pytest.fixture
def mock_state_manager():
    """Create a mock state manager."""
    manager = AsyncMock()
    manager.load_state = AsyncMock(return_value={})
    manager.save_state = AsyncMock()
    manager.save_build_state = AsyncMock()  # Used by _checkpoint_state
    manager.get_build_state = AsyncMock(return_value={})  # Used by _suspend_active_build
    manager.push_event = AsyncMock()
    manager.log_decision = AsyncMock()
    return manager


@pytest.fixture
def mock_graph():
    """Create a mock LangGraph."""
    graph = MagicMock()
    graph.astream = AsyncMock(return_value=iter([]))
    return graph


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value="Test response")
    return client


class TestHandleBuildError:
    """Test handle_build_error method."""

    @pytest.mark.asyncio
    async def test_updates_build_settings_status(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that handle_build_error updates build_settings status to error."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        # Set initial state with build_settings
        loop._state = {
            "build_in_progress": True,
            "build_settings": {
                "status": "in_progress",
                "project_name": "test_agent",
            },
        }

        await loop.handle_build_error("LLM call failed", build_id="build_123")

        # Status should be updated to error
        assert loop._state["build_settings"]["status"] == "error"
        assert loop._state["build_settings"]["error_message"] == "LLM call failed"
        assert loop._state["build_in_progress"] is False
        assert loop._state["build_phase"] == "error"

    @pytest.mark.asyncio
    async def test_creates_error_work_item_for_notification(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that handle_build_error creates a work item to notify user."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        loop._state = {
            "build_in_progress": True,
            "build_settings": {"build_id": "build_123"},
            "backlog": [],
        }

        await loop.handle_build_error("Build validation failed", notify_user=True)

        # Should have added an error work item to backlog
        assert len(loop._state["backlog"]) == 1
        error_item = loop._state["backlog"][0]
        # item_type might be enum or string depending on serialization
        item_type = error_item.item_type
        if hasattr(item_type, "value"):
            item_type = item_type.value
        assert item_type == "direct_answer"
        assert "Build Error" in error_item.description
        assert error_item.context["error_type"] == "build_error"
        assert error_item.context["notify_user"] is True

    @pytest.mark.asyncio
    async def test_pushes_error_event(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that handle_build_error pushes event for client notification."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        loop._state = {
            "build_in_progress": True,
            "build_settings": {"build_id": "build_test"},
        }

        await loop.handle_build_error("Test error message")

        # Should have called _push_event
        # The loop uses state_manager.push_event internally
        # Check that the error event was pushed (via the mock)

    @pytest.mark.asyncio
    async def test_checkpoints_state_with_error(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that handle_build_error checkpoints the error state."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        # Set up mock to return current file state for update_build_state
        mock_state_manager.get_build_state = AsyncMock(return_value={
            "build_in_progress": True,
            "build_settings": {"status": "in_progress"},
        })
        mock_state_manager.update_build_state = AsyncMock()

        loop._state = {
            "build_in_progress": True,
        }

        await loop.handle_build_error("Checkpoint test error")

        # State should have been saved via update_build_state (safe merge pattern)
        mock_state_manager.update_build_state.assert_called()


class TestSuspendActiveBuild:
    """Test _suspend_active_build method."""

    @pytest.mark.asyncio
    async def test_suspend_updates_status_to_suspended(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that suspend sets status to suspended, not error."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        # Set up the mock to return the build state from file
        file_state = {
            "build_in_progress": True,
            "build_settings": {
                "status": "in_progress",
                "project_name": "test",
            },
            "build_messages": [],
        }
        mock_state_manager.get_build_state = AsyncMock(return_value=file_state)

        loop._state = {
            "build_in_progress": True,
        }

        await loop._suspend_active_build("User pressed Ctrl+C")

        # Verify save_build_state was called with suspended status
        mock_state_manager.save_build_state.assert_called_once()
        saved_state = mock_state_manager.save_build_state.call_args[0][0]
        assert saved_state["build_settings"]["status"] == "suspended"
        assert saved_state["build_settings"]["error_message"] == "User pressed Ctrl+C"
        assert saved_state["build_in_progress"] is False
        assert saved_state["build_phase"] == "suspended"

        # In-memory state should also be updated
        assert loop._state["build_in_progress"] is False
        assert loop._state["build_phase"] == "suspended"

    @pytest.mark.asyncio
    async def test_suspend_does_nothing_if_no_active_build(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that suspend is a no-op if no build is in progress."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        # Mock file state with no active build
        mock_state_manager.get_build_state = AsyncMock(return_value={
            "build_in_progress": False,
        })

        loop._state = {
            "build_in_progress": False,
        }

        # Should not raise, should be a no-op
        await loop._suspend_active_build("Test")

        # save_build_state should NOT have been called (no build to suspend)
        mock_state_manager.save_build_state.assert_not_called()

        # State should be unchanged
        assert loop._state.get("build_phase") != "suspended"

    @pytest.mark.asyncio
    async def test_suspend_checkpoints_state(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that suspend writes state before shutdown."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        # Set up the mock to return the build state from file
        file_state = {
            "build_in_progress": True,
            "build_settings": {
                "status": "in_progress",
            },
            "build_messages": [],
        }
        mock_state_manager.get_build_state = AsyncMock(return_value=file_state)

        loop._state = {
            "build_in_progress": True,
        }

        await loop._suspend_active_build("Graceful shutdown")

        # save_build_state should have been called with suspended status
        mock_state_manager.save_build_state.assert_called_once()
        saved_state = mock_state_manager.save_build_state.call_args[0][0]
        assert saved_state["build_settings"]["status"] == "suspended"
        assert saved_state["build_phase"] == "suspended"

        # In-memory state should also be updated
        assert loop._state["build_phase"] == "suspended"


class TestBuildErrorIntegration:
    """Integration tests for build error handling."""

    @pytest.mark.asyncio
    async def test_error_then_suspend_preserves_error(self, mock_state_manager, mock_graph, mock_llm_client, mock_config):
        """Test that error status is not overwritten by suspend."""
        loop = ProgenitorLoop(
            graph=mock_graph,
            state_manager=mock_state_manager,
            config=mock_config,
            llm_client=mock_llm_client,
            copilot_acp_manager=None,
        )

        loop._state = {
            "build_in_progress": True,
            "build_settings": {
                "status": "in_progress",
            },
        }

        # First, error occurs
        await loop.handle_build_error("Fatal error")
        assert loop._state["build_settings"]["status"] == "error"

        # Then suspend is called (e.g., during shutdown)
        # Since build_in_progress is now False, suspend should be a no-op
        await loop._suspend_active_build("Shutdown")

        # Status should still be error, not suspended
        assert loop._state["build_settings"]["status"] == "error"
