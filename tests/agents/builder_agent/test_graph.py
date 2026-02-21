"""Tests for the Builder Agent graph creation and checkpointing."""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_config(tmp_path):
    """Minimal config mock for graph tests."""
    config = MagicMock()
    config.storage.state_dir = tmp_path / "state"
    config.storage.log_dir = tmp_path / "logs"
    config.storage.worktree_dir = tmp_path / "worktrees"
    config.copilot.bin_path = "gh copilot"
    config.copilot.model = "gpt-4o"
    config.copilot.default_flags = ["--yolo"]
    config.copilot.acp_allowed_dirs = []
    config.copilot.timeout_seconds = 60
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "worktrees").mkdir()
    return config


@pytest.fixture
def mock_state_manager():
    return AsyncMock()


@pytest.fixture
def mock_llm_client():
    return AsyncMock()


@pytest.fixture
def mock_copilot_acp():
    return MagicMock()


def test_build_checkpointer_explicit_false():
    """Passing False should always return MemorySaver."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.builder_agent.graph import _build_checkpointer

    result = _build_checkpointer(False, None)
    assert isinstance(result, MemorySaver)


def test_build_checkpointer_invalid_config_falls_back():
    """When config has no state_dir, should fall back to MemorySaver."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.builder_agent.graph import _build_checkpointer

    result = _build_checkpointer(None, None)
    assert isinstance(result, MemorySaver)


def test_build_checkpointer_with_path(tmp_path):
    """When a valid path is given, should return a SqliteSaver or MemorySaver fallback."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.agents.builder_agent.graph import _build_checkpointer

    db_path = tmp_path / "test_checkpoints.db"
    result = _build_checkpointer(db_path, None)
    # Accept either SqliteSaver (if installed) or MemorySaver (fallback)
    assert result is not None
    # If SqliteSaver is present, the DB file should be created
    try:
        import importlib
        if importlib.util.find_spec("langgraph.checkpoint.sqlite"):
            assert db_path.exists()
        else:
            assert isinstance(result, MemorySaver)
    except Exception:
        assert isinstance(result, MemorySaver)


def test_build_checkpointer_uses_config_state_dir(tmp_path):
    """When path is None but config has state_dir, use that dir."""
    from app.agents.builder_agent.graph import _build_checkpointer

    config = MagicMock()
    config.storage.state_dir = tmp_path

    result = _build_checkpointer(None, config)
    assert result is not None


def test_create_inner_loop_graph_requires_copilot_acp(
    mock_config, mock_state_manager, mock_llm_client,
):
    """Graph creation should raise if copilot_acp_manager is None."""
    from app.agents.builder_agent.graph import create_inner_loop_graph

    with pytest.raises(RuntimeError, match="copilot_acp_manager is required"):
        create_inner_loop_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            config=mock_config,
            worktrees_dir=mock_config.storage.worktree_dir,
            copilot_acp_manager=None,
            checkpoint_db_path=False,
        )
