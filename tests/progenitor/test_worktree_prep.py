"""Tests for worktree_prep node."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.state.models import ProgenitorState


class TestWorktreePrepNode:
    """Tests for the worktree prep node."""

    def _make_state(self, **overrides) -> ProgenitorState:
        """Create a minimal state dict with overrides."""
        state: ProgenitorState = {
            "current_work_item": None,
            "build_settings": None,
            "messages": [],
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_skips_when_no_work_item(self):
        """Should return empty dict when no current_work_item."""
        from app.progenitor.nodes import worktree_prep

        node = worktree_prep.create_node(Path("/tmp/worktrees"))
        result = await node(self._make_state())
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_non_build_item_types(self):
        """Should skip items that aren't build types."""
        from app.progenitor.nodes import worktree_prep

        node = worktree_prep.create_node(Path("/tmp/worktrees"))
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "direct_answer",
                "description": "Just a question",
            },
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_no_project_directory(self):
        """Should skip when build_settings has no project_directory."""
        from app.progenitor.nodes import worktree_prep

        node = worktree_prep.create_node(Path("/tmp/worktrees"))
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "build_agent",
                "description": "Build something",
            },
            build_settings={"project_name": "test"},
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_creates_worktree_and_updates_state(self):
        """Should create worktree and update both work item and build_settings."""
        from app.progenitor.nodes import worktree_prep

        mock_worktree_path = Path("/tmp/worktrees/item-1")

        with patch.object(
            worktree_prep, "GitWorktreeManager"
        ) as MockGitManager:
            mock_manager = AsyncMock()
            mock_manager.create_worktree.return_value = mock_worktree_path
            MockGitManager.return_value = mock_manager

            # Also mock _setup_worktree_venv to avoid subprocess calls
            with patch.object(
                worktree_prep, "_setup_worktree_venv", new_callable=AsyncMock
            ) as mock_venv:
                mock_venv.return_value = True

                node = worktree_prep.create_node(Path("/tmp/worktrees"))
                state = self._make_state(
                    current_work_item={
                        "id": "item-1",
                        "item_type": "build_agent",
                        "description": "Build an agent",
                        "context": {},
                    },
                    build_settings={
                        "project_directory": "app/agents/test_agent",
                        "project_name": "test_agent",
                    },
                )

                # Ensure the agent_dir "exists" inside the worktree
                with patch("pathlib.Path.exists", return_value=True):
                    result = await node(state)

        # Should have created the worktree
        mock_manager.create_worktree.assert_called_once_with(
            work_item_id="item-1",
            branch_name="progenitor/build/item-1",
        )

        # Should have called venv setup
        mock_venv.assert_called_once()

        # Work item should be updated
        item = result["current_work_item"]
        assert item["worktree_path"] == str(mock_worktree_path)
        assert item["context"]["worktree_branch"] == "progenitor/build/item-1"
        assert item["context"]["venv_ready"] is True

        # Build settings should be updated
        settings = result["build_settings"]
        assert settings["worktree_path"] == str(mock_worktree_path)
        assert settings["worktree_branch"] == "progenitor/build/item-1"

    @pytest.mark.asyncio
    async def test_handles_worktree_creation_failure(self):
        """Should catch exceptions and store error in context."""
        from app.progenitor.nodes import worktree_prep

        with patch.object(
            worktree_prep, "GitWorktreeManager"
        ) as MockGitManager:
            mock_manager = AsyncMock()
            mock_manager.create_worktree.side_effect = RuntimeError("git failed")
            MockGitManager.return_value = mock_manager

            node = worktree_prep.create_node(Path("/tmp/worktrees"))
            state = self._make_state(
                current_work_item={
                    "id": "item-1",
                    "item_type": "build_agent",
                    "description": "Build something",
                    "context": {},
                },
                build_settings={
                    "project_directory": "app/agents/test_agent",
                    "project_name": "test_agent",
                },
            )
            result = await node(state)

        item = result["current_work_item"]
        assert "worktree_error" in item["context"]
        assert "git failed" in item["context"]["worktree_error"]

    @pytest.mark.asyncio
    async def test_handles_build_tool_type(self):
        """Should work for build_mcp items too."""
        from app.progenitor.nodes import worktree_prep

        mock_worktree_path = Path("/tmp/worktrees/tool-1")

        with patch.object(
            worktree_prep, "GitWorktreeManager"
        ) as MockGitManager:
            mock_manager = AsyncMock()
            mock_manager.create_worktree.return_value = mock_worktree_path
            MockGitManager.return_value = mock_manager

            with patch.object(
                worktree_prep, "_setup_worktree_venv", new_callable=AsyncMock
            ) as mock_venv:
                mock_venv.return_value = True

                node = worktree_prep.create_node(Path("/tmp/worktrees"))
                state = self._make_state(
                    current_work_item={
                        "id": "tool-1",
                        "item_type": "build_mcp",
                        "description": "Build an MCP server",
                        "context": {},
                    },
                    build_settings={
                        "project_directory": "app/mcp/my_tool",
                        "project_name": "my_tool",
                    },
                )

                with patch("pathlib.Path.exists", return_value=True):
                    result = await node(state)

        assert result["current_work_item"]["worktree_path"] == str(mock_worktree_path)

    @pytest.mark.asyncio
    async def test_venv_failure_recorded(self):
        """Should record venv_ready=False when venv setup fails."""
        from app.progenitor.nodes import worktree_prep

        mock_worktree_path = Path("/tmp/worktrees/item-1")

        with patch.object(
            worktree_prep, "GitWorktreeManager"
        ) as MockGitManager:
            mock_manager = AsyncMock()
            mock_manager.create_worktree.return_value = mock_worktree_path
            MockGitManager.return_value = mock_manager

            with patch.object(
                worktree_prep, "_setup_worktree_venv", new_callable=AsyncMock
            ) as mock_venv:
                mock_venv.return_value = False  # venv failed

                node = worktree_prep.create_node(Path("/tmp/worktrees"))
                state = self._make_state(
                    current_work_item={
                        "id": "item-1",
                        "item_type": "build_agent",
                        "description": "Build an agent",
                        "context": {},
                    },
                    build_settings={
                        "project_directory": "app/agents/test_agent",
                        "project_name": "test_agent",
                    },
                )

                with patch("pathlib.Path.exists", return_value=True):
                    result = await node(state)

        assert result["current_work_item"]["context"]["venv_ready"] is False


class TestSetupWorktreeVenv:
    """Tests for the _setup_worktree_venv helper."""

    @pytest.mark.asyncio
    async def test_skips_when_no_requirements(self, tmp_path):
        """Should return True if no requirements.txt exists."""
        from app.progenitor.nodes.worktree_prep import _setup_worktree_venv

        result = await _setup_worktree_venv(tmp_path)
        assert result is True

    @pytest.mark.asyncio
    async def test_creates_venv_and_installs(self, tmp_path):
        """Should create venv and install requirements."""
        from app.progenitor.nodes.worktree_prep import _setup_worktree_venv

        # Create a requirements.txt
        (tmp_path / "requirements.txt").write_text("requests>=2.0\n")

        # Mock subprocess calls
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"")
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            result = await _setup_worktree_venv(tmp_path)

        assert result is True
        # Should have been called twice: once for venv, once for pip install
        assert mock_exec.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_false_on_venv_failure(self, tmp_path):
        """Should return False if venv creation fails."""
        from app.progenitor.nodes.worktree_prep import _setup_worktree_venv

        (tmp_path / "requirements.txt").write_text("requests>=2.0\n")

        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", b"venv error")
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            result = await _setup_worktree_venv(tmp_path)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_pip_failure(self, tmp_path):
        """Should return False if pip install fails."""
        from app.progenitor.nodes.worktree_prep import _setup_worktree_venv

        (tmp_path / "requirements.txt").write_text("badpackage\n")

        # First call (venv) succeeds, second call (pip) fails
        venv_proc = AsyncMock()
        venv_proc.communicate.return_value = (b"", b"")
        venv_proc.returncode = 0

        pip_proc = AsyncMock()
        pip_proc.communicate.return_value = (b"", b"pip error")
        pip_proc.returncode = 1

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[venv_proc, pip_proc],
        ):
            result = await _setup_worktree_venv(tmp_path)

        assert result is False
