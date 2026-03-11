"""
Tests for InvokeAgentTool - generic async A2A dispatcher.

Verifies:
1. Tool metadata and auto-discovery
2. Fire-and-forget dispatch returns task_id immediately
3. Unknown agent fails hard (no fallbacks)
4. Missing user_input fails validation
5. Async dispatch propagates caller correlation IDs
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.tools.invoke_agent import InvokeAgentTool, _fire_and_forget
from app.progenitor.tools.protocol import ToolContext


class TestInvokeAgentTool:
    """Tests for InvokeAgentTool."""

    def test_tool_properties(self):
        """Tool metadata is correct."""
        tool = InvokeAgentTool()
        assert tool.name == "invoke_agent"
        assert "A2A" in tool.description or "agent" in tool.description.lower()
        assert isinstance(tool.keywords, list)
        assert len(tool.keywords) > 0

    @pytest.mark.asyncio
    async def test_execute_missing_user_input(self):
        """Fails when user_input is empty."""
        tool = InvokeAgentTool()
        mock_registry = MagicMock()
        mock_registry.find_by_name.return_value = {
            "url": "http://localhost:8108",
            "transport": "http",
            "status": "running",
        }
        context = ToolContext(
            user_input="",
            tool_args={"agent": "builder"},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
        )
        result = await tool.execute(context)
        assert not result.success

    @pytest.mark.asyncio
    async def test_execute_unknown_agent_fails(self):
        """Fails hard when agent is not in registry (no fallbacks)."""
        tool = InvokeAgentTool()
        mock_registry = MagicMock()
        mock_registry.find_by_name.return_value = None
        mock_registry.find_agent_by_capability.return_value = None
        context = ToolContext(
            user_input="Do something",
            tool_args={"agent": "nonexistent_agent"},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
        )
        result = await tool.execute(context)
        assert not result.success
        assert "nonexistent_agent" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_agents_json_when_registry_misses(self):
        tool = InvokeAgentTool()
        mock_registry = MagicMock()
        mock_registry.find_by_name.return_value = None
        mock_registry.find_agent_by_capability.return_value = None
        context = ToolContext(
            user_input="Fix typo in README",
            tool_args={"agent": "builder", "timeout": 1},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
        )
        with (
            patch(
                "app.progenitor.tools.invoke_agent._load_agents",
                return_value={"builder": {"url": "http://localhost:8108", "status": "running"}},
            ),
            patch("app.progenitor.tools.invoke_agent._fire_and_forget", new_callable=AsyncMock),
        ):
            result = await tool.execute(context)

        assert result.success
        assert result.data is not None
        assert result.data["agent"] == "builder"

    @pytest.mark.asyncio
    async def test_execute_dispatches_and_returns_task_id(self):
        """Returns task_id immediately without waiting for agent response."""
        tool = InvokeAgentTool()
        mock_registry = MagicMock()
        mock_registry.find_by_name.return_value = {
            "url": "http://localhost:8108",
            "transport": "http",
            "status": "running",
        }
        context = ToolContext(
            user_input="Fix typo in README",
            tool_args={"agent": "builder", "timeout": 1},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
        )
        with patch("app.progenitor.tools.invoke_agent._fire_and_forget", new_callable=AsyncMock):
            result = await tool.execute(context)
        assert result.success
        assert result.data is not None
        assert "task_id" in result.data
        assert result.data["agent"] == "builder"
        assert result.data["status"] == "dispatched"


def test_tool_auto_discovery():
    """invoke_agent is auto-discovered by the tool registry."""
    from app.progenitor.nodes.node_helpers.registry import get_system_tool

    tool = get_system_tool("invoke_agent")
    assert tool is not None
    assert tool.name == "invoke_agent"


@pytest.mark.asyncio
async def test_fire_and_forget_propagates_async_ids():
    mock_client = AsyncMock()
    mock_client.get_agent_card = AsyncMock(return_value=None)
    mock_client.call_agent = AsyncMock(return_value={"success": True})

    with patch("app.progenitor.tools.invoke_agent.A2AClient", return_value=mock_client):
        await _fire_and_forget(
            task_id="builder_agent_abc123",
            context_id="ctx-xyz",
            agent_name="builder_agent",
            agent_config={"url": "http://localhost:8107"},
            message="Build it",
            timeout=5,
        )

    call_kwargs = mock_client.call_agent.await_args.kwargs
    assert call_kwargs["context"]["dispatch_mode"] == "async"
    assert call_kwargs["context"]["a2a_task_id"] == "builder_agent_abc123"
    assert call_kwargs["context"]["a2a_context_id"] == "ctx-xyz"
