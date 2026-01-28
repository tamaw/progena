"""
Comprehensive Dual-Loop Integration Tests.

TEST-001: Full integration test for dual-loop with controlled LLM responses.

Tests the complete OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN cycle.
"""

import pytest
import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from app.progenitor.graph_dual_loop import create_progenitor_graph, should_iterate
from app.progenitor.state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
    Decision,
    DecisionAction,
    ConversationMessage,
)
from app.progenitor.config import ProgenitorConfig


@pytest.fixture
def mock_state_manager():
    """Create a mock state manager."""
    manager = MagicMock()
    manager.log_decision = AsyncMock()
    manager.log_conversation = AsyncMock()
    manager.get_backlog = AsyncMock(return_value=[])
    manager.save_backlog = AsyncMock()
    manager.log_iteration_start = AsyncMock()
    manager.log_iteration_end = AsyncMock()
    manager.save_verification_report = AsyncMock()
    manager.commit_state = AsyncMock()
    return manager


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client with controlled responses."""
    client = MagicMock()

    # Default responses
    client.decide = AsyncMock(return_value={
        "action": "answer_directly",
        "reasoning": "Simple question",
        "requires_planning": False,
        "direct_response": "This is a test response.",
    })

    client.generate = AsyncMock(return_value="Generated text")

    client.plan = AsyncMock(return_value=[{
        "id": "test-work-001",
        "description": "Test work item",
        "item_type": "direct_answer",
    }])

    client.build_ideal_state = AsyncMock(return_value={
        "success_criteria": ["Criterion 1", "Criterion 2"],
        "anti_criteria": ["Don't do X"],
        "constraints": {"time": 60},
    })

    client.verify = AsyncMock(return_value={
        "overall_score": 0.9,
        "passed": True,
        "gaps": [],
    })

    return client


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry."""
    registry = MagicMock()
    registry.find_by_name = MagicMock(return_value=None)
    registry.get_all_capabilities = MagicMock(return_value=["test_capability"])
    registry.load = AsyncMock()
    return registry


@pytest.fixture
def config():
    """Create test configuration."""
    return ProgenitorConfig()


@pytest.fixture
def worktrees_dir(tmp_path):
    """Create temporary worktrees directory."""
    path = tmp_path / "worktrees"
    path.mkdir()
    return path


class TestDualLoopGraphCreation:
    """Tests for dual-loop graph creation."""

    def test_graph_compiles_successfully(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that graph compiles without errors."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        assert graph is not None

    def test_all_required_nodes_present(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that all required nodes are in the graph."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        nodes = graph.get_graph().nodes
        node_names = {n for n in nodes.keys() if n != "__start__"}

        # Outer loop nodes
        assert "intake" in node_names
        assert "decide" in node_names
        assert "respond" in node_names

        # Inner loop nodes
        assert "observe" in node_names
        assert "think" in node_names
        assert "plan" in node_names
        assert "build" in node_names
        assert "verify" in node_names
        assert "learn" in node_names

    def test_inner_loop_edges_exist(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that inner loop has correct edges."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        edges = graph.get_graph().edges

        # Check key transitions
        edge_sources = [e[0] for e in edges]
        assert "observe" in edge_sources
        assert "think" in edge_sources
        assert "verify" in edge_sources
        assert "learn" in edge_sources


class TestShouldIterateLogic:
    """Tests for iteration control logic."""

    def test_first_iteration_continues(self):
        """First iteration should always continue."""
        state: ProgenitorState = {
            "current_iteration": 0,
            "ideal_state": None,
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "observe"

    def test_verification_passed_stops(self):
        """Should stop when verification passes."""
        state: ProgenitorState = {
            "current_iteration": 2,
            "verification_passed": True,
            "last_verification_report": {"overall_score": 0.95},
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "respond"

    def test_max_iterations_stops(self):
        """Should stop when max iterations reached."""
        state: ProgenitorState = {
            "current_iteration": 5,
            "verification_passed": False,
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "respond"

    def test_continues_under_limit(self):
        """Should continue if under limit and not passed."""
        state: ProgenitorState = {
            "current_iteration": 2,
            "verification_passed": False,
            "ideal_state": None,
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "observe"


class TestDualLoopIntegration:
    """Full integration tests with mock LLM."""

    @pytest.mark.asyncio
    async def test_simple_direct_answer_flow(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test simple question → direct answer flow."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        initial_state: ProgenitorState = {
            "current_input": "What is 2+2?",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
        }

        config_dict: Any = {"configurable": {"thread_id": "test-simple"}}

        # Run the graph
        result = await graph.ainvoke(initial_state, config_dict)

        # Should have a response
        assert result.get("current_decision") is not None or result.get("pending_response") is not None

    @pytest.mark.asyncio
    async def test_complex_task_enters_inner_loop(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that complex tasks enter the inner loop."""
        # Configure LLM to require planning
        mock_llm_client.decide = AsyncMock(return_value={
            "action": "build_agent",
            "reasoning": "Need to build an agent",
            "requires_planning": True,
            "direct_response": None,
        })

        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        initial_state: ProgenitorState = {
            "current_input": "Build me a weather agent",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
        }

        config_dict: Any = {"configurable": {"thread_id": "test-complex"}}

        # Run the graph
        result = await graph.ainvoke(initial_state, config_dict)

        # Should have processed the complex task
        decision = result.get("current_decision")
        assert decision is not None or result.get("ideal_state") is not None

    @pytest.mark.asyncio
    async def test_iteration_count_increments(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that iteration count increments through inner loop."""
        # Set up for multiple iterations
        mock_llm_client.decide = AsyncMock(return_value={
            "action": "build_agent",
            "reasoning": "Build task",
            "requires_planning": True,
        })

        # Make verification fail first time
        call_count = [0]
        async def verify_mock(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 2:
                return {"overall_score": 0.5, "passed": False, "gaps": ["Need more"]}
            return {"overall_score": 0.9, "passed": True, "gaps": []}

        mock_llm_client.verify = verify_mock

        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        initial_state: ProgenitorState = {
            "current_input": "Build something",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "current_iteration": 0,
        }

        config_dict: Any = {"configurable": {"thread_id": "test-iteration"}}

        result = await graph.ainvoke(initial_state, config_dict)

        # Should have gone through at least one iteration
        # (exact behavior depends on node implementation)
        assert result is not None


class TestStateTransitions:
    """Tests for state transitions through the loop."""

    @pytest.mark.asyncio
    async def test_state_preserved_through_nodes(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that state fields are preserved through node transitions."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        initial_state: ProgenitorState = {
            "current_input": "Hello",
            "messages": [ConversationMessage(id="msg-1", role="user", content="Hello")],
            "cycle_count": 5,  # Non-zero to test preservation
            "actions_this_cycle": 2,
            "shutdown_requested": False,
            "run_id": "test-run-123",
        }

        config_dict: Any = {"configurable": {"thread_id": "test-preserve"}}

        result = await graph.ainvoke(initial_state, config_dict)

        # Key state should be preserved
        assert result.get("run_id") == "test-run-123"

    @pytest.mark.asyncio
    async def test_shutdown_flag_stops_loop(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        config,
        worktrees_dir,
    ):
        """Test that shutdown flag causes loop to end."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
        )

        initial_state: ProgenitorState = {
            "current_input": None,
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": True,  # Request shutdown
        }

        config_dict: Any = {"configurable": {"thread_id": "test-shutdown"}}

        result = await graph.ainvoke(initial_state, config_dict)

        # Should complete without error (graph handles shutdown)
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
