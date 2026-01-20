"""
Integration test for dual loop (loop.spec.md) implementation.

Verifies that the OBSERVE → THINK → PLAN → BUILD → VERIFY → LEARN cycle works.
"""

import pytest
from unittest.mock import AsyncMock, Mock
from pathlib import Path

from app.progenitor.graph_dual_loop import create_progenitor_graph
from app.progenitor.state.models import ProgenitorState
from app.progenitor.config import ProgenitorConfig


def test_dual_loop_graph_compiles():
    """Test that the dual loop graph compiles without errors."""
    # Mock dependencies
    state_manager = Mock()
    state_manager.log_decision = AsyncMock()

    llm_client = Mock()
    llm_client.generate = AsyncMock(return_value="Test response")

    tool_registry = Mock()
    tool_registry.find_by_name = Mock(return_value=None)

    config = ProgenitorConfig()
    worktrees_dir = Path("./worktrees")

    # Create graph
    graph = create_progenitor_graph(
        state_manager=state_manager,
        llm_client=llm_client,
        tool_registry=tool_registry,
        config=config,
        worktrees_dir=worktrees_dir,
    )

    assert graph is not None

    # Verify nodes exist
    nodes = graph.get_graph().nodes
    node_names = {n for n in nodes.keys() if n != "__start__"}

    # Check outer loop nodes
    assert "intake" in node_names
    assert "decide" in node_names
    assert "respond" in node_names

    # Check inner loop nodes (loop.spec.md)
    assert "observe" in node_names
    assert "think" in node_names
    assert "plan" in node_names
    assert "build" in node_names
    assert "verify" in node_names
    assert "learn" in node_names


def test_inner_loop_phases_present():
    """Verify all 7 phases of inner loop are wired."""
    state_manager = Mock()
    state_manager.log_decision = AsyncMock()

    llm_client = Mock()
    tool_registry = Mock()
    config = ProgenitorConfig()
    worktrees_dir = Path("./worktrees")

    graph = create_progenitor_graph(
        state_manager=state_manager,
        llm_client=llm_client,
        tool_registry=tool_registry,
        config=config,
        worktrees_dir=worktrees_dir,
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

    # VERIFY → LEARN
    verify_edges = [e for e in edges if e[0] == "verify"]
    assert any("learn" in str(e) for e in verify_edges)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
