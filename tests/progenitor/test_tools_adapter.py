"""
Tests for Phase 2: Tool adapter — wrapping Progenitor tools for DeepAgent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.agent.tools_adapter import (
    INTERNAL_TOOLS,
    _format_tool_result,
    _make_tool_context,
    create_deepagent_tools,
)
from app.progenitor.tools.protocol import ToolContext, ToolResult


class TestToolContext:
    """Tests for ToolContext creation."""

    def test_make_context_defaults(self):
        """Context created with defaults."""
        ctx = _make_tool_context()
        assert ctx.user_input == ""
        assert ctx.tool_args is None
        assert ctx.tool_registry is None

    def test_make_context_with_args(self):
        """Context created with all arguments."""
        registry = MagicMock()
        llm = MagicMock()
        ctx = _make_tool_context(
            tool_registry=registry,
            llm_client=llm,
            user_input="test input",
            tool_args={"key": "val"},
        )
        assert ctx.tool_registry is registry
        assert ctx.llm_client is llm
        assert ctx.user_input == "test input"
        assert ctx.tool_args == {"key": "val"}


class TestFormatToolResult:
    """Tests for ToolResult formatting."""

    def test_format_success_with_data(self):
        """Successful result with data returns JSON."""
        result = ToolResult.ok({"key": "value"})
        output = _format_tool_result(result)
        assert '"key": "value"' in output

    def test_format_success_no_data(self):
        """Successful result without data returns 'Success'."""
        result = ToolResult(success=True)
        output = _format_tool_result(result)
        assert output == "Success"

    def test_format_failure(self):
        """Failed result returns error message."""
        result = ToolResult.fail("something went wrong")
        output = _format_tool_result(result)
        assert "Error: something went wrong" in output


class TestCreateDeepagentTools:
    """Tests for the tool wrapping pipeline."""

    def test_creates_tools_from_registry(self):
        """Tools are created from discovered system tools."""
        tools = create_deepagent_tools()
        assert isinstance(tools, list)
        # Should have wrapped at least some tools
        assert len(tools) > 0

    def test_skips_internal_tools(self):
        """Internal tools are not wrapped."""
        tools = create_deepagent_tools()
        tool_names = {t.name for t in tools}
        for internal in INTERNAL_TOOLS:
            assert internal not in tool_names, f"Internal tool {internal} should be skipped"

    def test_tool_has_name_and_description(self):
        """Each wrapped tool has name and description."""
        tools = create_deepagent_tools()
        for t in tools:
            assert hasattr(t, "name"), f"Tool missing name: {t}"
            assert hasattr(t, "description"), f"Tool {t.name} missing description"
            assert len(t.name) > 0
            assert len(t.description) > 0

    def test_known_tools_present(self):
        """Key tools that should be wrapped are present."""
        tools = create_deepagent_tools()
        tool_names = {t.name for t in tools}
        expected = {"current_time", "list_agents", "list_tools", "list_schedule"}
        for name in expected:
            assert name in tool_names, f"Expected tool {name} not found"


class TestToolExecution:
    """Tests for wrapped tool execution."""

    @pytest.mark.asyncio
    async def test_current_time_tool_works(self):
        """The current_time tool returns time data when invoked."""
        tools = create_deepagent_tools()
        time_tool = next(t for t in tools if t.name == "current_time")

        # Invoke the wrapped tool
        result = await time_tool.ainvoke({"request": ""})
        assert "utc_time" in result
        assert "UTC" in result

    @pytest.mark.asyncio
    async def test_list_tools_returns_data(self):
        """The list_tools tool returns tool listing."""
        tools = create_deepagent_tools()
        list_tool = next(t for t in tools if t.name == "list_tools")

        result = await list_tool.ainvoke({"request": ""})
        # Should return some kind of tool listing
        assert isinstance(result, str)
        assert len(result) > 0
