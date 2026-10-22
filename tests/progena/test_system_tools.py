"""System tool tests.

System tool implementations live in :mod:`src.progena.tools` and are
registered/executed via :mod:`src.progena.tools.system_registry`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

import src.progena.tools.list_agents as list_agents_module
from src.progena.tools.list_agents import ListAgentsTool
from src.progena.tools.list_tools import ListToolsTool
from src.progena.tools.protocol import BaseSystemTool, ToolContext, ToolResult
from src.progena.tools.system_registry import execute_system_tool


class TestBaseSystemTool:
    """Tests for BaseSystemTool abstract class."""

    def test_parameters_are_derived_from_args_model(self):
        """args_model should be the source of truth for public tool parameters."""

        class TestArgs(BaseModel):
            value: str = Field(description="Primary value")
            note: str | None = Field(default=None, description="Optional note")

        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test description"

            @property
            def args_model(self):
                return TestArgs

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        params = TestTool().parameters

        assert params["type"] == "object"
        assert params["required"] == ["value"]
        assert params["properties"]["value"]["type"] == "string"
        assert params["properties"]["note"]["type"] == "string"
        assert "anyOf" not in params["properties"]["note"]
        assert "title" not in params


class TestListToolsTool:
    """Tests for ListToolsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListToolsTool()

    async def test_execute_with_registry(self, tool):
        """Should list tools from registry."""
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = [
            {"name": "agent1", "description": "First agent", "status": "running"},
        ]
        mock_registry.get_tools.return_value = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
        ]

        mock_llm = MagicMock()
        mock_llm.mcp_runtime = MagicMock()
        mock_llm.mcp_runtime.get_tool_registry_entries.return_value = [
            {"name": "remote_tool", "description": "Remote MCP tool"},
        ]

        ctx = ToolContext(
            tool_registry=mock_registry,
            llm_client=mock_llm,
            state={},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True
        assert result.data is not None
        assert "system_tools" in result.data
        assert "agents" in result.data
        assert "mcp_tools" in result.data

        mcp_names = {t.get("name") for t in result.data["mcp_tools"]}
        assert mcp_names == {"tool1", "tool2", "remote_tool"}

        system_names = {t.get("name") for t in result.data["system_tools"]}
        assert len(system_names) > 0

        agent_names = {a.get("name") for a in result.data["agents"]}
        assert "agent1" in agent_names


class TestListAgentsTool:
    """Tests for ListAgentsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListAgentsTool()

    @pytest.mark.asyncio
    async def test_execute_refreshes_cards_before_listing(self, tool):
        """list_agents should refresh metadata and health before returning output."""
        mock_registry = MagicMock()
        mock_registry.load = AsyncMock()
        mock_registry.get_agents.return_value = []
        ctx = ToolContext(tool_registry=mock_registry, llm_client=MagicMock())

        refresh_mock = AsyncMock(
            return_value={"agent_card_refresh": {}, "agent_health_refresh": {}},
        )

        with patch.object(list_agents_module, "refresh_agent_metadata", refresh_mock):
            result = await tool.execute(ctx)

        assert result.success is True
        mock_registry.load.assert_awaited_once()
        refresh_mock.assert_awaited_once_with(mock_registry)


class TestExecuteSystemTool:
    """Tests for execute_system_tool registry function."""

    async def test_execute_known_tool(self):
        """Should execute known tool and return tuple."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        # execute_system_tool returns (result_dict, error, state_updates, should_retry_slow_path)
        result, error, state_updates, should_retry = await execute_system_tool(
            "current_time",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None
        assert should_retry is False

    async def test_execute_unknown_tool(self):
        """Should return error for unknown tool."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error, state_updates, should_retry = await execute_system_tool(
            "unknown_tool_xyz",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is not None
        assert "Unknown system tool" in error
        assert result is None
        assert should_retry is False

    async def test_execute_list_tools(self):
        """Should execute list_tools with registry."""
        mock_registry = MagicMock()
        mock_registry.get_all_tools.return_value = []
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error, state_updates, should_retry = await execute_system_tool(
            "list_tools",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None
        assert should_retry is False
