"""
Tests for InvokeAgentTool - generic async A2A dispatcher.

Verifies:
1. Tool metadata and auto-discovery
2. Fire-and-forget dispatch returns task_id immediately
3. Unknown agent fails hard (no fallbacks)
4. Missing user_input fails validation
5. Async dispatch metadata is passed to the remote agent
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.progenitor.tools.invoke_agent import InvokeAgentTool
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
        result = await tool.execute(context)
        assert result.success
        assert result.data is not None
        assert "task_id" in result.data
        assert result.data["agent"] == "builder"
        assert result.data["status"] == "dispatched"

    @pytest.mark.asyncio
    async def test_execute_passes_async_task_metadata_to_remote_call(self, monkeypatch):
        """Background dispatch should forward correlation IDs to the remote A2A call."""
        tool = InvokeAgentTool()
        mock_registry = MagicMock()
        mock_registry.find_by_name.return_value = {
            "url": "http://localhost:8108",
            "transport": "http",
            "status": "running",
        }

        seen: dict[str, object] = {}

        class _FakeA2AClient:
            async def get_agent_card(self, cfg, timeout=5.0):  # noqa: ARG002
                return None

            async def call_agent(self, agent_config, request, context=None, timeout=60.0):  # noqa: ARG002
                seen["agent_config"] = agent_config
                seen["request"] = request
                seen["context"] = context or {}
                return {"success": True, "result": {"status": {"state": "submitted"}}}

        monkeypatch.setattr("app.progenitor.tools.invoke_agent.A2AClient", _FakeA2AClient)

        context = ToolContext(
            user_input="Fix typo in README",
            tool_args={"agent": "builder", "timeout": 1},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
        )
        result = await tool.execute(context)

        assert result.success
        assert result.data is not None
        task_id = result.data["task_id"]

        for _ in range(20):
            await asyncio.sleep(0.01)
            if seen:
                break

        assert seen["request"] == "Fix typo in README"
        assert isinstance(seen["context"], dict)
        assert seen["context"]["caller"] == "progenitor"
        assert seen["context"]["dispatch_mode"] == "async"
        assert seen["context"]["a2a_task_id"] == task_id
        assert isinstance(seen["context"]["a2a_context_id"], str)


def test_tool_auto_discovery():
    """invoke_agent is auto-discovered by the tool registry."""
    from app.progenitor.nodes.node_helpers.registry import get_system_tool

    tool = get_system_tool("invoke_agent")
    assert tool is not None
    assert tool.name == "invoke_agent"
