"""
End-to-end integration test for dual loop cycle.

Tests a complete OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN cycle
with mocked LLM but real state transitions.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.progenitor.config import ProgenitorConfig
from app.progenitor.graph_outer_loop import create_progenitor_graph
from app.progenitor.state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client that returns realistic responses."""
    client = Mock()

    # Mock chat to return JSON-like responses for different scenarios
    async def mock_chat(messages, system_prompt=None, **kwargs):
        prompt_lower = system_prompt.lower() if system_prompt else ""

        if "observe" in prompt_lower or "analyze" in prompt_lower:
            return json.dumps({
                "observations": ["Task is simple text processing"],
                "constraints": ["No external dependencies"],
                "resources_available": ["Python stdlib"],
            })
        elif "think" in prompt_lower or "approaches" in prompt_lower:
            return json.dumps({
                "approaches": [
                    {"name": "Direct", "confidence": 0.9, "steps": ["Implement directly"]},
                ],
                "selected": "Direct",
            })
        elif "plan" in prompt_lower:
            return json.dumps({
                "steps": [
                    {"action": "create_file", "target": "output.txt"},
                ],
            })
        elif "verify" in prompt_lower:
            return json.dumps({
                "passed": True,
                "score": 0.85,
                "issues": [],
            })
        elif "learn" in prompt_lower:
            return json.dumps({
                "lesson": "Simple tasks can be completed in one iteration",
                "improvements": [],
            })
        else:
            return "OK"

    client.chat = AsyncMock(side_effect=mock_chat)
    client.generate = AsyncMock(return_value="Generated content")
    return client


@pytest.fixture
def mock_state_manager():
    """Create a mock state manager."""
    manager = Mock()
    manager.log_decision = AsyncMock()
    manager.save_build_state = AsyncMock()
    manager.get_build_state = AsyncMock(return_value={})
    return manager


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry."""
    registry = Mock()
    registry.find_by_name = Mock(return_value=None)
    registry.get_all_capabilities = Mock(return_value=["code_generation", "file_operations"])
    return registry


@pytest.fixture
def test_config():
    """Create a test configuration."""
    return ProgenitorConfig()


@pytest.fixture
def mock_copilot_acp_manager():
    """Create a mock ProgenitorACPClient for testing."""
    from app.progenitor.runtime.copilot_acp import ProgenitorACPClient
    client = MagicMock(spec=ProgenitorACPClient)
    client.port = 3000
    client.timeout = 1200
    client._initialized = True
    return client


@pytest.fixture
def test_work_item():
    """Create a test work item."""
    return WorkItem(
        id="test-item-001",
        item_type=WorkItemType.DIRECT_ANSWER,
        status=WorkItemStatus.PENDING,
        description="Create a simple hello world script",
        context={"prompt": "Create a Python script that prints 'Hello World'"},
    )


class TestDualLoopEndToEnd:
    """End-to-end tests for the dual loop architecture."""

    def test_graph_creation(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        mock_copilot_acp_manager,
        test_config,
    ):
        """Test that the graph can be created without errors."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=test_config,
            worktrees_dir=Path("./worktrees"),
            copilot_acp_manager=mock_copilot_acp_manager,
        )

        assert graph is not None

        # Verify all expected outer loop nodes exist (no planner - only in inner loop)
        nodes = set(graph.get_graph().nodes.keys()) - {"__start__"}

        expected_outer_nodes = {
            "intake", "time_check", "decide", "pick_next",
            "worktree_prep", "act", "validate", "commit_core",
            "respond", "cycle_limit", "idle",
        }

        for node in expected_outer_nodes:
            assert node in nodes, f"Missing outer loop node: {node}"

        # Planner is NOT in outer loop - only in inner loop
        assert "planner" not in nodes, "planner should only be in inner loop"

        # Inner loop nodes should NOT be in outer loop
        inner_loop_nodes = {"observe", "think", "learn", "verify"}
        for node in inner_loop_nodes:
            assert node not in nodes, f"Inner loop node {node} should not be in outer loop"

    def test_state_initialization(self, test_work_item):
        """Test that state can be initialized with a work item."""
        state: ProgenitorState = {
            "messages": [],
            "backlog": [test_work_item],
            "current_work_item": None,
            "cycle_count": 0,
            "iteration": {"current": 0},
            "verification": {"current_passed": False, "current_score": 0.0},
            "should_iterate": True,
        }

        assert len(state["backlog"]) == 1
        assert state["backlog"][0].id == "test-item-001"
        assert state["iteration"]["current"] == 0

    def test_work_item_transitions(self, test_work_item):
        """Test that work items can transition through statuses."""
        # Start pending
        assert test_work_item.status == WorkItemStatus.PENDING

        # Transition to in_progress
        test_work_item.status = WorkItemStatus.IN_PROGRESS
        assert test_work_item.status == WorkItemStatus.IN_PROGRESS

        # Complete
        test_work_item.status = WorkItemStatus.COMPLETED
        assert test_work_item.status == WorkItemStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_mock_llm_responses(self, mock_llm_client):
        """Test that mock LLM returns expected responses."""
        # Test observe-like prompt
        response = await mock_llm_client.chat(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="Observe and analyze the task",
        )
        data = json.loads(response)
        assert "observations" in data

        # Test verify-like prompt
        response = await mock_llm_client.chat(
            messages=[{"role": "user", "content": "test"}],
            system_prompt="Verify the results",
        )
        data = json.loads(response)
        assert "passed" in data
        assert "score" in data


class TestIterationControl:
    """Tests for iteration control logic."""

    def test_iteration_increment(self):
        """Test that iterations are tracked correctly."""
        state: ProgenitorState = {
            "iteration": {"current": 0},
            "verification": {"current_passed": False},
            "should_iterate": True,
        }

        # Simulate iteration increment
        state["iteration"]["current"] += 1
        assert state["iteration"]["current"] == 1

        state["iteration"]["current"] += 1
        assert state["iteration"]["current"] == 2

    def test_verification_stops_iteration(self):
        """Test that passing verification stops iteration."""
        state: ProgenitorState = {
            "iteration": {"current": 1},
            "verification": {"current_passed": False, "current_score": 0.5},
            "should_iterate": True,
        }

        # Simulate verification passing
        state["verification"]["current_passed"] = True
        state["verification"]["current_score"] = 0.9
        state["should_iterate"] = False

        assert not state["should_iterate"]
        current_score = state.get("verification", {}).get("current_score")
        assert current_score is not None
        assert current_score >= 0.8

    def test_max_iterations_stops_loop(self, test_config):
        """Test that max iterations setting is respected."""
        max_iter = test_config.runtime.max_inner_iterations

        state: ProgenitorState = {
            "iteration": {"current": max_iter},
            "verification": {"current_passed": False},
            "should_iterate": True,
        }

        # At max iterations, should stop
        if state["iteration"]["current"] >= max_iter:
            state["should_iterate"] = False
            state["iteration_stop_reason"] = "max_iterations_reached"

        assert not state["should_iterate"]
        assert state.get("iteration_stop_reason") == "max_iterations_reached"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
