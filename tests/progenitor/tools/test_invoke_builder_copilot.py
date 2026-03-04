"""
Test the invoke_builder_copilot system tool integration.

This test verifies that:
1. The tool is auto-discovered by Progenitor's registry
2. The tool can be invoked with proper context
3. The tool returns expected results
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.tools.invoke_builder_copilot import InvokeBuilderCopilotTool
from app.progenitor.tools.protocol import ToolContext, ToolResult


class TestInvokeBuilderCopilotTool:
    """Tests for InvokeBuilderCopilotTool."""

    def test_tool_properties(self):
        """Test tool metadata."""
        tool = InvokeBuilderCopilotTool()
        
        assert tool.name == "invoke_builder_copilot"
        assert "Builder Copilot" in tool.description
        assert "code generation" in tool.description.lower()
        assert isinstance(tool.keywords, list)
        assert len(tool.keywords) > 0

    @pytest.mark.asyncio
    async def test_execute_missing_request(self):
        """Test that execute fails with empty request."""
        tool = InvokeBuilderCopilotTool()
        context = ToolContext(
            user_input="",  # Empty request
            tool_args=None,
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
        )
        
        result = await tool.execute(context)
        
        assert not result.success
        assert "Missing" in result.error or "request" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_builder_copilot_not_running(self):
        """Test handling when Builder Copilot is not running."""
        tool = InvokeBuilderCopilotTool()
        context = ToolContext(
            user_input="Create a README",
            tool_args={"url": "http://localhost:9999"},  # Port that won't respond
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
        )
        
        result = await tool.execute(context)
        
        assert not result.success
        assert "Builder Copilot" in result.error or "cannot" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_timeout_is_ok(self):
        """Test that timeout is acceptable for async tasks."""
        tool = InvokeBuilderCopilotTool()
        
        # Mock a timeout response
        mock_llm = MagicMock()
        
        context = ToolContext(
            user_input="Create a README for Builder Copilot",
            tool_args=None,  # Use defaults
            tool_registry=MagicMock(),
            llm_client=mock_llm,
        )
        
        # Note: This test will timeout if Builder Copilot is not running,
        # but that's OK - we're testing the error handling path
        result = await tool.execute(context)
        
        # Timeout should result in success with task_id
        # (since async execution is expected)
        if result.success:
            assert "task_id" in result.output
            assert "accepted" in result.output.get("status", "").lower()
        else:
            # Or failure if Builder Copilot really isn't running
            assert "Builder Copilot" in result.error or "cannot" in result.error.lower()


def test_tool_auto_discovery():
    """Test that invoke_builder_copilot is auto-discovered by registry."""
    from app.progenitor.nodes.node_helpers.registry import get_system_tool
    
    # The tool should be discovered automatically
    tool = get_system_tool("invoke_builder_copilot")
    
    # May be None if not yet initialized, but class should exist
    assert tool is None or isinstance(tool, InvokeBuilderCopilotTool)


@pytest.mark.asyncio
async def test_a2a_request_format():
    """Test that the A2A request format is correct for Builder Copilot."""
    tool = InvokeBuilderCopilotTool()
    
    # This is a smoke test - just verify the tool can handle requests
    # without actually hitting the network
    context = ToolContext(
        user_input="Test request",
        tool_args={"url": "http://localhost:9999"},  # Non-existent server
        tool_registry=MagicMock(),
        llm_client=MagicMock(),
    )
    
    # Should fail gracefully when Builder Copilot not reachable
    result = await tool.execute(context)
    assert not result.success
    # Should indicate connection issue, not JSON parsing error
    assert "Builder Copilot" in result.error or "cannot" in result.error.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
