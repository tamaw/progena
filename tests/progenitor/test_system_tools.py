"""
Tests for app/progenitor/nodes/system_tools/

Verifies system tool protocol, implementations, and registry.
"""

import pytest
from typing import cast
from unittest.mock import MagicMock, AsyncMock, patch
from app.progenitor.nodes.system_tools import (
    ToolContext,
    ToolResult,
    SystemTool,
    BaseSystemTool,
    execute_system_tool,
    format_tool_result,
    ListToolsTool,
    BuildStatusTool,
    ListScheduleTool,
    ScheduleTaskTool,
    RediscoverAgentsTool,
    ListAgentsTool,
    CurrentTimeTool,
    SelfReflectTool,
)
from app.progenitor.state.models import ProgenitorState


class TestToolContext:
    """Tests for ToolContext dataclass."""

    def test_creation_with_required_fields(self):
        """Should create with required fields."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
        )
        assert ctx.tool_registry is not None
        assert ctx.llm_client is not None
        assert ctx.state is None
        assert ctx.state_manager is None

    def test_full_creation(self):
        """Should create with all fields."""
        tool_registry = MagicMock()
        llm_client = MagicMock()
        state_manager = MagicMock()

        ctx = ToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=cast(ProgenitorState, {"key": "value"}),
            state_manager=state_manager,
        )

        assert ctx.tool_registry is tool_registry
        assert ctx.llm_client is llm_client
        assert ctx.state == {"key": "value"}
        assert ctx.state_manager is state_manager


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_success_result(self):
        """Should create success result."""
        result = ToolResult.ok(data={"key": "value"})
        assert result.success is True
        assert result.data == {"key": "value"}
        assert result.error is None

    def test_failure_result(self):
        """Should create failure result."""
        result = ToolResult.fail(error="Operation failed")
        assert result.success is False
        assert result.error == "Operation failed"

    def test_direct_creation(self):
        """Should create result directly."""
        result = ToolResult(
            success=True,
            data={"count": 5},
        )
        assert result.success is True
        assert result.data == {"count": 5}


class TestBaseSystemTool:
    """Tests for BaseSystemTool abstract class."""

    def test_subclass_must_implement_name(self):
        """Subclass must implement name property."""
        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test tool"

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        tool = TestTool()
        assert tool.name == "test_tool"

    def test_subclass_must_implement_description(self):
        """Subclass must implement description property."""
        class TestTool(BaseSystemTool):
            @property
            def name(self):
                return "test_tool"

            @property
            def description(self):
                return "Test description"

            async def execute(self, context: ToolContext) -> ToolResult:
                return ToolResult.ok(data={"status": "done"})

        tool = TestTool()
        assert tool.description == "Test description"


class TestListToolsTool:
    """Tests for ListToolsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListToolsTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_tools"

    def test_description(self, tool):
        """Should have description."""
        assert len(tool.description) > 0

    async def test_execute_with_registry(self, tool):
        """Should list tools from registry."""
        mock_registry = MagicMock()
        mock_registry.get_tools.return_value = [
            {"name": "tool1", "description": "First tool"},
            {"name": "tool2", "description": "Second tool"},
        ]

        ctx = ToolContext(
            tool_registry=mock_registry,
            llm_client=MagicMock(),
            state={},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True


class TestBuildStatusTool:
    """Tests for BuildStatusTool implementation."""

    @pytest.fixture
    def tool(self):
        return BuildStatusTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "build_status"

    async def test_execute_no_build(self, tool):
        """Should report no build when none active."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={"build_in_progress": False},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True
        # Message should indicate no active build

    async def test_execute_with_build(self, tool):
        """Should report build status when active."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={
                "build_in_progress": True,
                "build_settings": {"project_name": "test_agent"},
            },
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True


class TestCurrentTimeTool:
    """Tests for CurrentTimeTool implementation."""

    @pytest.fixture
    def tool(self):
        return CurrentTimeTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "current_time"

    async def test_execute_returns_time(self, tool):
        """Should return current time."""
        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={},
            state_manager=MagicMock(),
        )
        result = await tool.execute(ctx)
        assert result.success is True


class TestListScheduleTool:
    """Tests for ListScheduleTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListScheduleTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_schedule"

    async def test_execute_empty_schedule(self, tool):
        """Should handle empty schedule."""
        mock_state_manager = MagicMock()
        mock_state_manager.get_schedule = AsyncMock(return_value=[])

        ctx = ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state={"schedule": []},
            state_manager=mock_state_manager,
        )
        result = await tool.execute(ctx)
        assert result.success is True


class TestScheduleTaskTool:
    """Tests for ScheduleTaskTool implementation."""

    @pytest.fixture
    def tool(self):
        return ScheduleTaskTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "schedule_task"


class TestRediscoverAgentsTool:
    """Tests for RediscoverAgentsTool implementation."""

    @pytest.fixture
    def tool(self):
        return RediscoverAgentsTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "rediscover_agents"


class TestListAgentsTool:
    """Tests for ListAgentsTool implementation."""

    @pytest.fixture
    def tool(self):
        return ListAgentsTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "list_agents"


class TestSelfReflectTool:
    """Tests for SelfReflectTool implementation."""

    @pytest.fixture
    def tool(self):
        return SelfReflectTool()

    def test_name(self, tool):
        """Should have correct name."""
        assert tool.name == "self_reflect"


class TestFormatToolResult:
    """Tests for format_tool_result function."""

    def test_format_list_tools(self):
        """Should format list_tools result."""
        result = {
            "system_tools": [{"name": "build_status", "description": "Check build status"}],
            "mcp_tools": [],
            "total_count": 1,
        }
        formatted = format_tool_result("list_tools", result)
        assert "Available Tools" in formatted
        assert "build_status" in formatted

    def test_format_build_status(self):
        """Should format build_status result."""
        result = {"status": "no_build", "message": "No active build"}
        formatted = format_tool_result("build_status", result)
        assert isinstance(formatted, str)

    def test_format_unknown_tool(self):
        """Should handle unknown tool with JSON fallback."""
        result = {"key": "value"}
        formatted = format_tool_result("unknown_tool", result)
        assert "Result" in formatted
        assert "value" in formatted


class TestExecuteSystemTool:
    """Tests for execute_system_tool registry function."""

    async def test_execute_known_tool(self):
        """Should execute known tool and return tuple."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        # execute_system_tool returns (result_dict, error)
        result, error = await execute_system_tool(
            "current_time",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None

    async def test_execute_unknown_tool(self):
        """Should return error for unknown tool."""
        mock_registry = MagicMock()
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error = await execute_system_tool(
            "unknown_tool_xyz",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is not None
        assert "Unknown system tool" in error
        assert result is None

    async def test_execute_list_tools(self):
        """Should execute list_tools with registry."""
        mock_registry = MagicMock()
        mock_registry.get_all_tools.return_value = []
        mock_llm = MagicMock()
        state_manager = AsyncMock()

        result, error = await execute_system_tool(
            "list_tools",
            mock_registry,
            mock_llm,
            state={},
            state_manager=state_manager,
        )
        assert error is None
        assert result is not None
