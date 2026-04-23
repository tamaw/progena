"""Tests for register node local registry behavior."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.state.models import ProgenitorState


class TestRegisterNode:
    """Tests for the register_node function."""

    def _make_state(self, **overrides) -> ProgenitorState:
        state: ProgenitorState = {
            "current_work_item": None,
            "validation_result_v2": None,
            "messages": [],
        }
        state.update(overrides)
        return state

    def _make_deps(self):
        """Create mock dependencies for register node."""
        tool_registry = MagicMock()
        tool_registry.get_agents.return_value = []
        tool_registry.register_agent = AsyncMock()
        tool_registry.register_tool = AsyncMock()
        tool_registry.find_by_name.return_value = None

        state_manager = AsyncMock()

        config = MagicMock()
        config.storage.worktree_dir = Path("/tmp/worktrees")

        return tool_registry, state_manager, config

    @pytest.mark.asyncio
    async def test_skips_when_no_work_item(self):
        """Should return empty dict when no current_work_item."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        node = register.create_node(registry, sm, config)
        result = await node(self._make_state())
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_validation_failed(self):
        """Should skip when validation has blocking issues."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        node = register.create_node(registry, sm, config)
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "build_agent",
                "description": "test",
                "result": {"spec": {"name": "test"}},
            },
            validation_result_v2={"has_blocking_issues": True},
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_registers_agent_and_tests_it(self):
        """Should register and test a newly built agent."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()

        with patch.object(register, "AgentRunner") as MockRunner:
            mock_runner = AsyncMock()
            test_result = MagicMock()
            test_result.success = True
            test_result.response = "OK"
            test_result.error = None
            test_result.duration_ms = 100
            mock_runner.test_agent.return_value = test_result
            MockRunner.return_value = mock_runner

            node = register.create_node(registry, sm, config)
            state = self._make_state(
                current_work_item={
                    "id": "item-1",
                    "item_type": "build_agent",
                    "description": "test",
                    "result": {
                        "spec": {
                            "name": "weather_agent",
                            "description": "Weather agent",
                            "capabilities": ["forecast"],
                        }
                    },
                },
                validation_result_v2={"has_blocking_issues": False},
            )

            await node(state)

        # Should have registered with tool registry
        registry.register_agent.assert_called_once()

        # Should have tested the agent
        mock_runner.test_agent.assert_called_once()

        # Should have logged decisions
        assert sm.log_decision.call_count >= 2  # tested + registered

    @pytest.mark.asyncio
    async def test_registers_mcp_tool(self):
        """Should register MCP tool in local mcp state."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()

        with patch.object(register, "AgentRunner") as MockRunner:
            MockRunner.return_value = AsyncMock()
            node = register.create_node(registry, sm, config)
            state = self._make_state(
                current_work_item={
                    "id": "item-2",
                    "item_type": "build_mcp",
                    "description": "test",
                    "result": {
                        "spec": {
                            "name": "search_tool",
                            "description": "Web search",
                            "capabilities": ["search"],
                        }
                    },
                },
                validation_result_v2={"has_blocking_issues": False},
            )

            await node(state)

        # Should have registered tool
        registry.register_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_registration_exception(self):
        """Should catch and log exceptions, returning empty dict."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        registry.register_agent.side_effect = RuntimeError("registry write failed")

        with patch.object(register, "AgentRunner") as MockRunner:
            MockRunner.return_value = AsyncMock()

            node = register.create_node(registry, sm, config)
            state = self._make_state(
                current_work_item={
                    "id": "item-1",
                    "item_type": "build_agent",
                    "description": "test",
                    "result": {"spec": {"name": "test"}},
                },
                validation_result_v2={"has_blocking_issues": False},
            )

            result = await node(state)

        # Should return empty (error caught)
        assert result == {}
