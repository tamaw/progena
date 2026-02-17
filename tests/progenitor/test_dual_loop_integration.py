"""
Integration test for outer and inner loop implementations.

- Outer loop: Intake → TimeCheck → Decide → Planner → PickNext → Act → Respond → CycleLimit → Idle
- Inner loop: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN (runs in background)
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.progenitor.config import ProgenitorConfig
from app.progenitor.graph_inner_loop import create_inner_loop_graph
from app.progenitor.graph_outer_loop import create_progenitor_graph


def _create_mock_copilot_acp_manager():
    """Create a mock ProgenitorACPClient for testing."""
    from app.progenitor.runtime.copilot_acp import ProgenitorACPClient
    client = MagicMock(spec=ProgenitorACPClient)
    client.port = 3000
    client.timeout = 1200
    client._initialized = True
    return client


def test_outer_loop_graph_compiles():
    """Test that the outer loop graph compiles without errors."""
    # Mock dependencies
    state_manager = Mock()
    state_manager.log_decision = AsyncMock()

    llm_client = Mock()
    llm_client.chat = AsyncMock(return_value="Test response")
    llm_client.generate = AsyncMock(return_value="Test response")

    tool_registry = Mock()
    tool_registry.find_by_name = Mock(return_value=None)

    config = ProgenitorConfig()
    worktrees_dir = Path("./worktrees")

    # Create outer loop graph
    graph = create_progenitor_graph(
        state_manager=state_manager,
        llm_client=llm_client,
        tool_registry=tool_registry,
        config=config,
        worktrees_dir=worktrees_dir,
        copilot_acp_manager=_create_mock_copilot_acp_manager(),
    )

    assert graph is not None

    # Verify outer loop nodes exist
    nodes = graph.get_graph().nodes
    node_names = {n for n in nodes if n != "__start__"}

    # Check outer loop nodes (no planner - only in inner loop)
    assert "intake" in node_names
    assert "time_check" in node_names
    assert "chat" in node_names
    assert "pick_next" in node_names
    assert "act" in node_names
    assert "validate" in node_names
    assert "commit_core" in node_names
    assert "respond" in node_names
    assert "cycle_limit" in node_names
    assert "idle" in node_names
    # planner is NOT in outer loop - only in inner loop
    assert "planner" not in node_names

    # Inner loop nodes should NOT be in outer loop
    assert "observe" not in node_names
    assert "think" not in node_names
    assert "learn" not in node_names


def test_inner_loop_graph_compiles():
    """Test that the inner loop graph compiles without errors."""
    state_manager = Mock()
    state_manager.log_decision = AsyncMock()
    state_manager.save_build_state = AsyncMock()

    llm_client = Mock()
    llm_client.generate = AsyncMock(return_value="Test response")

    config = ProgenitorConfig()
    worktrees_dir = Path("./worktrees")

    # Create inner loop graph
    graph = create_inner_loop_graph(
        state_manager=state_manager,
        llm_client=llm_client,
        config=config,
        worktrees_dir=worktrees_dir,
        copilot_acp_manager=_create_mock_copilot_acp_manager(),
    )

    assert graph is not None

    # Verify inner loop nodes exist
    nodes = graph.get_graph().nodes
    node_names = {n for n in nodes if n != "__start__"}

    # Check all 7 inner loop phases
    assert "observe" in node_names
    assert "think" in node_names
    assert "plan" in node_names
    assert "build" in node_names
    assert "execute" in node_names
    assert "verify" in node_names
    assert "learn" in node_names


def test_inner_loop_phases_connected():
    """Verify inner loop phases are properly connected."""
    state_manager = Mock()
    state_manager.log_decision = AsyncMock()
    state_manager.save_build_state = AsyncMock()

    llm_client = Mock()
    config = ProgenitorConfig()
    worktrees_dir = Path("./worktrees")

    graph = create_inner_loop_graph(
        state_manager=state_manager,
        llm_client=llm_client,
        config=config,
        worktrees_dir=worktrees_dir,
        copilot_acp_manager=_create_mock_copilot_acp_manager(),
    )

    # Get edges to verify flow
    edges = graph.get_graph().edges

    # Check critical inner loop connections exist
    # OBSERVE → THINK
    observe_edges = [e for e in edges if e[0] == "observe"]
    assert any("think" in str(e) for e in observe_edges)

    # THINK → PLAN
    think_edges = [e for e in edges if e[0] == "think"]
    assert any("plan" in str(e) for e in think_edges)

    # VERIFY → LEARN (conditional)
    verify_edges = [e for e in edges if e[0] == "verify"]
    assert any("learn" in str(e) for e in verify_edges)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
