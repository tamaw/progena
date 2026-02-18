"""Tests for publish node."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.progenitor.state.models import ProgenitorState


class TestPublishNode:
    """Tests for the publish node."""

    def _make_state(self, **overrides) -> ProgenitorState:
        state: ProgenitorState = {
            "current_work_item": None,
            "build_settings": {},
            "verification": {},
            "build_messages": [],
            "messages": [],
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_skips_when_no_work_item(self):
        """Should return empty dict when no current_work_item."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()
        node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
        result = await node(self._make_state())
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_verification_not_passed(self):
        """Should skip when verification hasn't passed."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()
        node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "build_agent",
                "description": "test",
                "worktree_path": "/tmp/worktrees/item-1",
            },
            verification={"current_passed": False},
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_no_worktree_path(self):
        """Should skip when work item has no worktree_path."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()
        node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "build_agent",
                "description": "test",
                "worktree_path": None,
            },
            verification={"current_passed": True},
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_creates_pr_on_verification_pass(self):
        """Should commit, push, and create PR when verification passed."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()

        with patch.object(publish, "GitWorktreeManager") as MockGit:
            mock_git = AsyncMock()
            mock_git.commit_changes.return_value = "abc123"
            MockGit.return_value = mock_git

            with patch.object(
                publish, "gh_create_pr_from_worktree", new_callable=AsyncMock
            ) as mock_gh:
                mock_gh.return_value = (True, "https://github.com/org/repo/pull/42")

                node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
                state = self._make_state(
                    current_work_item={
                        "id": "item-1",
                        "item_type": "build_agent",
                        "description": "Build a weather agent",
                        "worktree_path": "/tmp/worktrees/item-1",
                        "context": {},
                    },
                    verification={"current_passed": True, "current_score": 0.95},
                    build_settings={
                        "worktree_branch": "progenitor/build/item-1",
                        "project_name": "weather_agent",
                    },
                )

                result = await node(state)

        # Should have committed changes
        mock_git.commit_changes.assert_called_once()

        # Should have called gh_create_pr_from_worktree
        mock_gh.assert_called_once()
        call_kwargs = mock_gh.call_args.kwargs
        assert call_kwargs["branch"] == "progenitor/build/item-1"

        # Should update work item with PR info
        item = result["current_work_item"]
        assert item["context"]["pr_url"] == "https://github.com/org/repo/pull/42"
        assert item["context"]["pr_status"] == "created"

        # Should log the PR creation
        mock_sm.log_decision.assert_called()

        # Should append build_messages
        assert len(result["build_messages"]) == 1
        assert result["build_messages"][0]["phase"] == "publish"
        assert result["build_messages"][0]["pr_url"] == "https://github.com/org/repo/pull/42"

    @pytest.mark.asyncio
    async def test_handles_pr_creation_failure(self):
        """Should handle PR creation failure gracefully."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()

        with patch.object(publish, "GitWorktreeManager") as MockGit:
            mock_git = AsyncMock()
            mock_git.commit_changes.return_value = "abc123"
            MockGit.return_value = mock_git

            with patch.object(
                publish, "gh_create_pr_from_worktree", new_callable=AsyncMock
            ) as mock_gh:
                mock_gh.return_value = (False, "Branch not found")

                node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
                state = self._make_state(
                    current_work_item={
                        "id": "item-1",
                        "item_type": "build_agent",
                        "description": "Build something",
                        "worktree_path": "/tmp/worktrees/item-1",
                        "context": {},
                    },
                    verification={"current_passed": True},
                    build_settings={
                        "worktree_branch": "progenitor/build/item-1",
                        "project_name": "test",
                    },
                )

                result = await node(state)

        item = result["current_work_item"]
        assert item["context"]["pr_status"] == "failed"
        assert item["context"]["pr_url"] is None

    @pytest.mark.asyncio
    async def test_handles_exception_during_publish(self):
        """Should catch exceptions and return error in build_messages."""
        from app.progenitor.nodes import publish

        mock_sm = AsyncMock()

        with patch.object(publish, "GitWorktreeManager") as MockGit:
            mock_git = AsyncMock()
            mock_git.commit_changes.side_effect = RuntimeError("commit failed")
            MockGit.return_value = mock_git

            node = publish.create_node(mock_sm, Path("/tmp/worktrees"))
            state = self._make_state(
                current_work_item={
                    "id": "item-1",
                    "item_type": "build_agent",
                    "description": "Build something",
                    "worktree_path": "/tmp/worktrees/item-1",
                    "context": {},
                },
                verification={"current_passed": True},
                build_settings={
                    "worktree_branch": "progenitor/build/item-1",
                    "project_name": "test",
                },
            )

            result = await node(state)

        # Should have logged the failure
        mock_sm.log_decision.assert_called()

        # Should have error in build_messages
        assert len(result["build_messages"]) == 1
        assert "commit failed" in result["build_messages"][0]["error"]


class TestBuildPrBody:
    """Tests for the _build_pr_body helper."""

    def test_generates_agent_pr_body(self):
        from app.progenitor.nodes.publish import _build_pr_body

        body = _build_pr_body(
            item_id="item-1",
            item_type="build_agent",
            project_name="weather_agent",
            description="A weather forecasting agent",
            verification={"current_score": 0.95, "target_score": 0.80},
            build_settings={},
        )

        assert "Agent: weather_agent" in body
        assert "weather forecasting agent" in body
        assert "0.95" in body
        assert "app/agents/weather_agent" in body

    def test_generates_mcp_pr_body(self):
        from app.progenitor.nodes.publish import _build_pr_body

        body = _build_pr_body(
            item_id="item-2",
            item_type="build_tool",
            project_name="search_tool",
            description="Web search MCP server",
            verification={"current_score": 0.88, "target_score": 0.80},
            build_settings={},
        )

        assert "MCP Server: search_tool" in body
        assert "app/mcp/search_tool" in body
