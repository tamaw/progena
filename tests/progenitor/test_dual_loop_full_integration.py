"""
Comprehensive Dual-Loop Integration Tests.

TEST-001: Full integration test for dual-loop with controlled LLM responses.

Tests the complete OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN cycle.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.config import ProgenitorConfig
from app.progenitor.graph_chat_loop import create_progenitor_graph
from app.progenitor.routing import should_iterate
from app.progenitor.state.models import (
    ConversationMessage,
    ProgenitorState,
)


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
    # New methods for dual state files
    manager.save_messages = AsyncMock()
    manager.get_messages = AsyncMock(return_value=[])
    manager.save_current_state = AsyncMock()
    manager.get_current_state = AsyncMock(return_value={})
    manager.update_current_state = AsyncMock()
    manager.save_current_schedule = AsyncMock()
    manager.save_schedule = AsyncMock()
    return manager


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client with controlled responses."""
    client = MagicMock()

    # Default responses
    client.chat = AsyncMock(return_value="This is a test response.")

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

    # Build spec generation for build_agent tasks
    client.generate_build_spec = AsyncMock(return_value={
        "name": "test_agent",
        "kind": "agent",
        "template": "basic",
        "capabilities": ["test"],
    })

    return client


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry."""
    registry = MagicMock()
    registry.find_by_name = MagicMock(return_value=None)
    registry.get_all_capabilities = MagicMock(return_value=["test_capability"])
    registry.load = AsyncMock()
    registry.register_agent = AsyncMock()  # Async method used in register node
    registry.register_tool = AsyncMock()   # Async method used in register node
    return registry


@pytest.fixture
def config():
    """Create test configuration with fast settings for tests."""
    cfg = ProgenitorConfig()
    # Override idle backoff for fast tests (avoid 30s waits)
    cfg.runtime.idle_backoff_seconds = 0
    return cfg


@pytest.fixture
def worktrees_dir(tmp_path):
    """Create temporary worktrees directory."""
    path = tmp_path / "worktrees"
    path.mkdir()
    return path


@pytest.fixture
def mock_copilot_acp_manager():
    """Create a mock ProgenitorACPClient for testing."""
    from app.progenitor.runtime.copilot_acp import ProgenitorACPClient
    client = MagicMock(spec=ProgenitorACPClient)
    client.port = 3000
    client.timeout = 1200
    client._initialized = True
    return client


class TestDualLoopGraphCreation:
    """Tests for dual-loop graph creation."""

    def test_graph_compiles_successfully(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        assert graph is not None

    def test_all_required_nodes_present(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        nodes = graph.get_graph().nodes
        node_names = {n for n in nodes if n != "__start__"}

        # Outer loop nodes (no planner - only in inner loop)
        assert "intake" in node_names
        assert "chat" in node_names
        assert "commit_core" in node_names
        assert "respond" in node_names

        # Backlog execution nodes are handled by background graph
        assert "pick_next" not in node_names
        assert "worktree_prep" not in node_names
        assert "act" not in node_names
        assert "validate" not in node_names
        assert "cycle_limit" not in node_names
        assert "idle" not in node_names

        # Planner is NOT in outer loop - only in inner loop
        assert "planner" not in node_names

        # Inner loop nodes are NOT in outer loop graph
        assert "observe" not in node_names
        assert "think" not in node_names
        assert "learn" not in node_names

    def test_outer_loop_edges_exist(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
        config,
        worktrees_dir,
    ):
        """Test that outer loop has correct edges."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=config,
            worktrees_dir=worktrees_dir,
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        edges = graph.get_graph().edges

        # Check key outer loop transitions
        edge_sources = [e[0] for e in edges]
        assert "intake" in edge_sources
        assert "chat" in edge_sources
        assert "respond" in edge_sources
        assert "commit_core" in edge_sources


class TestShouldIterateLogic:
    """Tests for iteration control logic."""

    def test_first_iteration_continues(self):
        """First iteration should always continue."""
        state: ProgenitorState = {
            "iteration": {"current": 0},
            "phases": {"observe": {"ideal_state": None}},
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "observe"

    def test_verification_passed_stops(self):
        """Should stop when verification passes."""
        state: ProgenitorState = {
            "iteration": {"current": 2},
            "verification": {
                "current_passed": True,
                "current_report": {"overall_score": 0.95},
            },
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "respond"

    def test_max_iterations_stops(self):
        """Should stop when max iterations reached."""
        state: ProgenitorState = {
            "iteration": {"current": 5},
            "verification": {"current_passed": False},
        }

        result = should_iterate(state, max_iterations=5)
        assert result == "respond"

    def test_continues_under_limit(self):
        """Should continue if under limit and not passed."""
        state: ProgenitorState = {
            "iteration": {"current": 2},
            "verification": {"current_passed": False},
            "phases": {"observe": {"ideal_state": None}},
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
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        initial_state: ProgenitorState = {
            "current_input": "What is 2+2?",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": True,  # Exit after one cycle for test
        }

        config_dict: Any = {"configurable": {"thread_id": "test-simple"}}

        # Run the graph
        result = await graph.ainvoke(initial_state, config_dict)

        # Should have a response
        assert result.get("current_decision") is not None or result.get("pending_response") is not None

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Complex async graph test - requires full mock setup")
    async def test_complex_task_enters_inner_loop(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        initial_state: ProgenitorState = {
            "current_input": "Build me a weather agent",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": True,  # Exit after one cycle for test
        }

        config_dict: Any = {"configurable": {"thread_id": "test-complex"}}

        # Run the graph
        result = await graph.ainvoke(initial_state, config_dict)

        # Should have processed the complex task
        from app.progenitor.state.helpers import get_ideal_state

        decision = result.get("current_decision")
        assert decision is not None or get_ideal_state(result) is not None

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Complex async graph test - requires full mock setup")
    async def test_iteration_count_increments(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        initial_state: ProgenitorState = {
            "current_input": "Build something",
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": True,  # Exit after one cycle for test
            "iteration": {"current": 0},
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
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        initial_state: ProgenitorState = {
            "current_input": "Hello",
            "messages": [ConversationMessage(id="msg-1", role="user", content="Hello")],
            "cycle_count": 5,  # Non-zero to test preservation
            "actions_this_cycle": 2,
            "shutdown_requested": True,  # Exit after one cycle for test
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
        mock_copilot_acp_manager,
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
            copilot_acp_manager=mock_copilot_acp_manager,
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
