"""Tests for dependency injection container."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


class TestNodeDependencies(unittest.TestCase):
    """Test NodeDependencies container."""

    def test_import(self):
        """Test that dependency container can be imported."""
        from app.progenitor.nodes import NodeDependencies
        assert NodeDependencies is not None

    def test_protocols_can_be_imported(self):
        """Test that protocols can be imported."""
        from app.progenitor.nodes import (
            LLMClientProtocol,
            StateManagerProtocol,
            ToolRegistryProtocol,
        )

        assert LLMClientProtocol is not None
        assert StateManagerProtocol is not None
        assert ToolRegistryProtocol is not None

    def test_create_dependencies_with_mocks(self):
        """Test creating dependencies with mocked components."""
        from app.progenitor.config import ProgenitorConfig
        from app.progenitor.nodes import NodeDependencies

        llm_client = MagicMock()
        llm_client.decide = AsyncMock()

        state_manager = MagicMock()
        state_manager.get_backlog = AsyncMock(return_value=[])

        tool_registry = MagicMock()
        tool_registry.get_all_capabilities = MagicMock(return_value=[])

        config = ProgenitorConfig()

        deps = NodeDependencies(
            llm_client=llm_client,
            state_manager=state_manager,
            tool_registry=tool_registry,
            config=config,
        )

        assert deps.llm_client is llm_client
        assert deps.state_manager is state_manager
        assert deps.tool_registry is tool_registry
        assert deps.config is config

    def test_default_paths_from_config(self):
        """Test that paths default from config."""
        from app.progenitor.config import ProgenitorConfig
        from app.progenitor.nodes import NodeDependencies

        config = ProgenitorConfig()

        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
            tool_registry=MagicMock(),
            config=config,
        )

        assert deps.worktrees_dir == config.storage.worktree_dir
        assert deps.copilot_config_path == config.copilot.config_path

    def test_custom_paths_override_config(self):
        """Test that explicit paths override config defaults."""
        from app.progenitor.config import ProgenitorConfig
        from app.progenitor.nodes import NodeDependencies

        custom_worktrees = Path("/custom/worktrees")
        custom_copilot = Path("/custom/copilot.json")

        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
            tool_registry=MagicMock(),
            config=ProgenitorConfig(),
            worktrees_dir=custom_worktrees,
            copilot_config_path=custom_copilot,
        )

        assert deps.worktrees_dir == custom_worktrees
        assert deps.copilot_config_path == custom_copilot


class TestCreateTestDependencies(unittest.TestCase):
    """Test the create_test_dependencies helper."""

    def test_creates_with_all_defaults(self):
        """Test creating test dependencies with all defaults."""
        from app.progenitor.nodes import create_test_dependencies

        deps = create_test_dependencies()

        assert deps.llm_client is not None
        assert deps.state_manager is not None
        assert deps.tool_registry is not None
        assert deps.config is not None

    def test_accepts_custom_llm_client(self):
        """Test providing custom LLM client."""
        from app.progenitor.nodes import create_test_dependencies

        custom_client = MagicMock()
        deps = create_test_dependencies(llm_client=custom_client)

        assert deps.llm_client is custom_client

    def test_accepts_custom_state_manager(self):
        """Test providing custom state manager."""
        from app.progenitor.nodes import create_test_dependencies

        custom_manager = MagicMock()
        deps = create_test_dependencies(state_manager=custom_manager)

        assert deps.state_manager is custom_manager

    def test_accepts_custom_tool_registry(self):
        """Test providing custom tool registry."""
        from app.progenitor.nodes import create_test_dependencies

        custom_registry = MagicMock()
        deps = create_test_dependencies(tool_registry=custom_registry)

        assert deps.tool_registry is custom_registry


class TestCreateAllNodes(unittest.TestCase):
    """Test the create_all_nodes function."""

    def test_import_create_all_nodes(self):
        """Test that create_all_nodes can be imported."""
        from app.progenitor.nodes import create_all_nodes
        assert create_all_nodes is not None

    def test_import_create_node_subset(self):
        """Test that create_node_subset can be imported."""
        from app.progenitor.nodes import create_node_subset
        assert create_node_subset is not None


class TestProtocolConformance(unittest.TestCase):
    """Test that protocols work with isinstance checks."""

    def test_llm_protocol_check(self):
        """Test LLM client protocol isinstance check."""
        from app.progenitor.nodes import LLMClientProtocol

        class MockLLM:
            async def decide(self, user_input, conversation_history, tool_context):
                return {}

            async def plan(self, user_input, decision, current_backlog):
                return []

            async def generate_build_spec(self, work_item, context):
                return {}

            async def generate(self, prompt, system_prompt=None):
                return ""

        mock = MockLLM()
        assert isinstance(mock, LLMClientProtocol)

    def test_state_manager_protocol_check(self):
        """Test state manager protocol isinstance check."""
        from app.progenitor.nodes import StateManagerProtocol

        class MockStateManager:
            async def get_backlog(self):
                return []

            async def save_backlog(self, backlog):
                pass

            async def log_decision(self, decision):
                pass

            async def log_conversation(self, message):
                pass

        mock = MockStateManager()
        assert isinstance(mock, StateManagerProtocol)

    def test_tool_registry_protocol_check(self):
        """Test tool registry protocol isinstance check."""
        from app.progenitor.nodes import ToolRegistryProtocol

        class MockRegistry:
            def get_all_capabilities(self):
                return []

            def find_by_name(self, name):
                return None

        mock = MockRegistry()
        assert isinstance(mock, ToolRegistryProtocol)


class TestCustomNodesOverride(unittest.TestCase):
    """Test custom node overrides in dependencies."""

    def test_custom_nodes_empty_by_default(self):
        """Test that custom_nodes is empty by default."""
        from app.progenitor.nodes import create_test_dependencies

        deps = create_test_dependencies()
        assert deps.custom_nodes == {}

    def test_custom_nodes_can_be_provided(self):
        """Test providing custom nodes."""
        from app.progenitor.config import ProgenitorConfig
        from app.progenitor.nodes import NodeDependencies

        async def custom_node(state):
            return {"custom": True}

        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
            tool_registry=MagicMock(),
            config=ProgenitorConfig(),
            custom_nodes={"my_custom": custom_node},
        )

        assert "my_custom" in deps.custom_nodes
        assert deps.custom_nodes["my_custom"] is custom_node


if __name__ == "__main__":
    unittest.main()
