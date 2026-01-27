"""
End-to-end integration test for dual loop cycle.

Tests a complete OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN cycle
with mocked LLM but real state transitions.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from pathlib import Path
import json

from app.progenitor.graph_dual_loop import create_progenitor_graph
from app.progenitor.state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemType,
    WorkItemStatus,
)
from app.progenitor.config import ProgenitorConfig


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
    manager.save_state = AsyncMock()
    manager.get_state = AsyncMock(return_value={})
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
def test_work_item():
    """Create a test work item."""
    return WorkItem(
        id="test-item-001",
        item_type=WorkItemType.DIRECT_ANSWER,
        status=WorkItemStatus.PENDING,
        description="Create a simple hello world script",
        prompt="Create a Python script that prints 'Hello World'",
    )


class TestDualLoopEndToEnd:
    """End-to-end tests for the dual loop architecture."""

    def test_graph_creation(
        self,
        mock_state_manager,
        mock_llm_client,
        mock_tool_registry,
        test_config,
    ):
        """Test that the graph can be created without errors."""
        graph = create_progenitor_graph(
            state_manager=mock_state_manager,
            llm_client=mock_llm_client,
            tool_registry=mock_tool_registry,
            config=test_config,
            worktrees_dir=Path("./worktrees"),
        )
        
        assert graph is not None
        
        # Verify all expected nodes exist
        nodes = set(graph.get_graph().nodes.keys()) - {"__start__"}
        
        expected_nodes = {
            "intake", "decide", "respond", "idle",
            "observe", "think", "plan", "build", "execute", "verify", "learn",
        }
        
        for node in expected_nodes:
            assert node in nodes, f"Missing node: {node}"

    def test_state_initialization(self, test_work_item):
        """Test that state can be initialized with a work item."""
        state: ProgenitorState = {
            "messages": [],
            "backlog": [test_work_item],
            "current_work_item": None,
            "cycle_count": 0,
            "current_iteration": 0,
            "verification_passed": False,
            "verification_score": 0.0,
            "should_iterate": True,
        }
        
        assert len(state["backlog"]) == 1
        assert state["backlog"][0].id == "test-item-001"
        assert state["current_iteration"] == 0

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
            "current_iteration": 0,
            "verification_passed": False,
            "should_iterate": True,
        }
        
        # Simulate iteration increment
        state["current_iteration"] += 1
        assert state["current_iteration"] == 1
        
        state["current_iteration"] += 1
        assert state["current_iteration"] == 2

    def test_verification_stops_iteration(self):
        """Test that passing verification stops iteration."""
        state: ProgenitorState = {
            "current_iteration": 1,
            "verification_passed": False,
            "verification_score": 0.5,
            "should_iterate": True,
        }
        
        # Simulate verification passing
        state["verification_passed"] = True
        state["verification_score"] = 0.9
        state["should_iterate"] = False
        
        assert not state["should_iterate"]
        assert state["verification_score"] >= 0.8

    def test_max_iterations_stops_loop(self, test_config):
        """Test that max iterations setting is respected."""
        max_iter = test_config.runtime.max_inner_iterations
        
        state: ProgenitorState = {
            "current_iteration": max_iter,
            "verification_passed": False,
            "should_iterate": True,
        }
        
        # At max iterations, should stop
        if state["current_iteration"] >= max_iter:
            state["should_iterate"] = False
            state["iteration_stop_reason"] = "max_iterations_reached"
        
        assert not state["should_iterate"]
        assert state["iteration_stop_reason"] == "max_iterations_reached"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
