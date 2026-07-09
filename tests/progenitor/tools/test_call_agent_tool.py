"""Tests for CallAgentTool - canonical outbound A2A dispatcher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.state.models import A2ATaskRecord
from app.progenitor.tools.call_agent import AgentSelection, CallAgentTool
from app.progenitor.tools.protocol import ToolContext


def _tracked_task(agent_name: str = "builder_agent") -> A2ATaskRecord:
    return A2ATaskRecord(
        task_id=f"{agent_name}_abc123",
        agent_name=agent_name,
        agent_url="http://localhost:8107",
        context_id="ctx-xyz",
        source="call_agent",
        request_text="Build it",
    )


class TestCallAgentTool:
    """Tests for CallAgentTool."""

    def test_tool_properties(self):
        tool = CallAgentTool()
        assert tool.name == "call_agent"
        assert "A2A" in tool.description or "agent" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_execute_missing_request(self):
        tool = CallAgentTool()
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = []
        context = ToolContext(
            user_input="",
            tool_args={"agent": "builder"},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
            state_manager=MagicMock(),
        )
        result = await tool.execute(context)
        assert not result.success

    @pytest.mark.asyncio
    async def test_execute_unknown_agent_fails(self):
        tool = CallAgentTool()
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = [{"name": "builder_agent"}]
        mock_registry.find_by_name.return_value = None
        mock_registry.find_agent_by_capability.return_value = None
        context = ToolContext(
            user_input="Do something",
            tool_args={"agent": "nonexistent_agent"},
            tool_registry=mock_registry,
            llm_client=MagicMock(),
            state_manager=MagicMock(),
        )
        result = await tool.execute(context)
        assert not result.success
        assert "nonexistent_agent" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_auto_routes_when_agent_missing(self):
        tool = CallAgentTool()
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = [
            {
                "name": "builder_agent",
                "url": "http://localhost:8107",
                "transport": "http",
                "status": "running",
                "capabilities": ["build"],
                "description": "Build and modify code.",
            }
        ]
        mock_registry.find_by_name.return_value = {
            "name": "builder_agent",
            "url": "http://localhost:8107",
            "transport": "http",
            "status": "running",
        }
        mock_llm = MagicMock()
        mock_llm.chat_structured = AsyncMock(
            return_value=AgentSelection(
                agent_name="builder_agent",
                message="Build the requested feature.",
                reasoning="It is the only matching deployed build agent.",
            )
        )
        context = ToolContext(
            user_input="Build the requested feature",
            tool_registry=mock_registry,
            llm_client=mock_llm,
            state_manager=MagicMock(),
        )

        with patch(
            "app.progenitor.tools.call_agent.dispatch_tracked_a2a_task",
            new_callable=AsyncMock,
            return_value=(
                {"success": True, "result": {"id": "builder_agent_abc123"}},
                _tracked_task(),
            ),
        ) as dispatch_mock:
            result = await tool.execute(context)

        assert result.success
        kwargs = dispatch_mock.await_args.kwargs
        assert kwargs["agent_name"] == "builder_agent"
        assert kwargs["request_text"] == "Build the requested feature."
        assert kwargs["request_metadata"]["routing_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_execute_dispatches_and_returns_task_id(self):
        tool = CallAgentTool()
        mock_registry = MagicMock()
        mock_registry.get_agents.return_value = [{"name": "builder"}]
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
            state_manager=MagicMock(),
        )
        with patch(
            "app.progenitor.tools.call_agent.dispatch_tracked_a2a_task",
            new_callable=AsyncMock,
            return_value=(
                {"success": True, "result": {"id": "builder_abc123"}},
                _tracked_task("builder"),
            ),
        ):
            result = await tool.execute(context)
        assert result.success
        assert result.data is not None
        assert "task_id" in result.data
        assert result.data["agent"] == "builder"
        assert result.data["status"] == "submitted"
        assert "get_a2a_task" in result.data["message"]


def test_tool_auto_discovery():
    from app.progenitor.tools.system_registry import get_system_tool

    tool = get_system_tool("call_agent")
    assert tool is not None
    assert tool.name == "call_agent"


@pytest.mark.asyncio
async def test_execute_dispatches_async_metadata():
    tool = CallAgentTool()
    mock_registry = MagicMock()
    mock_registry.get_agents.return_value = [{"name": "builder_agent"}]
    mock_registry.find_by_name.return_value = {
        "url": "http://localhost:8108",
        "transport": "http",
        "status": "running",
    }
    context = ToolContext(
        user_input="Build it",
        tool_args={"agent": "builder_agent", "timeout": 1},
        tool_registry=mock_registry,
        llm_client=MagicMock(),
        state_manager=MagicMock(),
    )

    with patch(
        "app.progenitor.tools.call_agent.dispatch_tracked_a2a_task",
        new_callable=AsyncMock,
        return_value=({"success": True, "result": {"id": "builder_agent_abc123"}}, _tracked_task()),
    ) as dispatch_mock:
        result = await tool.execute(context)

    assert result.success
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["request_metadata"]["dispatch_mode"] == "async"
    assert kwargs["request_metadata"]["caller"] == "progenitor"
    assert kwargs["request_metadata"]["routing_mode"] == "explicit"


@pytest.mark.asyncio
async def test_execute_dispatches_async_metadata_with_thread_id():
    tool = CallAgentTool()
    mock_registry = MagicMock()
    mock_registry.get_agents.return_value = [{"name": "builder_agent"}]
    mock_registry.find_by_name.return_value = {
        "url": "http://localhost:8108",
        "transport": "http",
        "status": "running",
    }
    context = ToolContext(
        user_input="Build it",
        tool_args={"agent": "builder_agent", "timeout": 1},
        tool_registry=mock_registry,
        llm_client=MagicMock(),
        state_manager=MagicMock(),
        state={"thread_id": "thread-abc"},
    )

    with patch(
        "app.progenitor.tools.call_agent.dispatch_tracked_a2a_task",
        new_callable=AsyncMock,
        return_value=({"success": True, "result": {"id": "builder_agent_abc123"}}, _tracked_task()),
    ) as dispatch_mock:
        result = await tool.execute(context)

    assert result.success
    kwargs = dispatch_mock.await_args.kwargs
    assert kwargs["request_metadata"]["thread_id"] == "thread-abc"
