"""
Tests for Phase 4: Custom middleware for DeepAgent.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from src.progena.agent.middleware import (
    AsyncSubagentTrackingMiddleware,
    DirectA2AFastPathMiddleware,
    DynamicMCPToolMiddleware,
    ToolTimingMiddleware,
)
from src.progena.async_subagents.models import AsyncSubagentTaskRecord


class TestDynamicMCPToolMiddleware:
    """Tests for dynamic external MCP tool injection."""

    @staticmethod
    def _base_tool(name: str = "current_time") -> StructuredTool:
        async def _run() -> dict[str, bool]:
            return {"ok": True}

        return StructuredTool.from_function(
            coroutine=_run,
            name=name,
            description=f"Tool: {name}",
        )

    @staticmethod
    def _request(*tools: StructuredTool) -> ModelRequest[Any]:
        return ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=list(tools),
        )

    @staticmethod
    def _tool_request(name: str, tool: StructuredTool | None = None) -> ToolCallRequest:
        return ToolCallRequest(
            tool_call={"name": name, "args": {}, "id": f"tc_{name}"},
            tool=tool,
            state={},
            runtime=MagicMock(),
        )

    async def test_injects_live_mcp_tools(self):
        mcp_tools = [self._base_tool("search_web")]

        mw = DynamicMCPToolMiddleware(mcp_tools_provider=lambda: mcp_tools)

        async def handler(request):
            return request

        result = await mw.awrap_model_call(
            self._request(self._base_tool()),
            handler,
        )

        names = {tool.name for tool in result.tools}
        assert {"current_time", "search_web"} == names

    async def test_uses_latest_mcp_tool_snapshot_each_call(self):
        mcp_tools = [self._base_tool("search_web")]

        mw = DynamicMCPToolMiddleware(mcp_tools_provider=lambda: mcp_tools)

        async def handler(request):
            return request

        first = await mw.awrap_model_call(self._request(self._base_tool()), handler)
        mcp_tools[:] = [self._base_tool("read_url")]
        second = await mw.awrap_model_call(self._request(self._base_tool()), handler)

        assert {tool.name for tool in first.tools} == {"current_time", "search_web"}
        assert {tool.name for tool in second.tools} == {"current_time", "read_url"}

    async def test_binds_dynamic_tool_for_execution(self):
        mcp_tools = [self._base_tool("read_url")]

        mw = DynamicMCPToolMiddleware(mcp_tools_provider=lambda: mcp_tools)
        request = self._tool_request("read_url")
        handler = AsyncMock(return_value="ok")

        result = await mw.awrap_tool_call(request, handler)

        assert result == "ok"
        bound_request = handler.call_args.args[0]
        assert bound_request is not request
        assert bound_request.tool is not None
        assert bound_request.tool.name == "read_url"
        assert request.tool is None

    async def test_leaves_unknown_tool_unbound(self):
        mcp_tools = [self._base_tool("read_url")]

        mw = DynamicMCPToolMiddleware(mcp_tools_provider=lambda: mcp_tools)
        request = self._tool_request("search_web")
        handler = AsyncMock(return_value="ok")

        result = await mw.awrap_tool_call(request, handler)

        assert result == "ok"
        assert handler.call_args.args[0] is request


class TestDirectA2AFastPathMiddleware:
    @staticmethod
    def _request(content: str, *, state: dict[str, Any] | None = None) -> ModelRequest[Any]:
        return ModelRequest(
            model=MagicMock(),
            messages=[HumanMessage(content=content)],
            tools=[],
            state=state or {},
        )

    async def test_non_matching_message_calls_handler(self):
        mw = DirectA2AFastPathMiddleware(tool_registry=MagicMock())
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="llm")]))

        result = await mw.awrap_model_call(self._request("hello"), handler)

        handler.assert_awaited_once()
        assert result.result[0].content == "llm"

    async def test_matching_prefix_bypasses_handler_and_returns_agent_text(self):
        registry = MagicMock()
        registry.find_by_name.return_value = {
            "name": "example_agent",
            "transport": "http",
            "url": "http://localhost:8109",
        }
        client = MagicMock()
        client.send_message = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "status": {
                        "message": {
                            "parts": [{"root": {"text": "Example result from example_agent!"}}],
                            "metadata": {"display_agent_name": "example_agent"},
                        }
                    }
                },
            }
        )
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)

        result = await mw.awrap_model_call(self._request("@example_agent hey"), AsyncMock())

        client.send_message.assert_awaited_once()
        assert result.result[0].content == "Example result from example_agent!"
        assert result.result[0].response_metadata["display_agent_name"] == "example_agent"

    async def test_prefix_only_matches_at_start_of_latest_user_message(self):
        registry = MagicMock()
        client = MagicMock()
        client.send_message = AsyncMock()
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="llm")]))

        result = await mw.awrap_model_call(self._request("hello @example_agent hey"), handler)

        handler.assert_awaited_once()
        client.send_message.assert_not_called()
        assert result.result[0].content == "llm"

    async def test_unknown_agent_returns_direct_error(self):
        registry = MagicMock()
        registry.find_by_name.return_value = None
        mw = DirectA2AFastPathMiddleware(tool_registry=registry)

        result = await mw.awrap_model_call(self._request("@missing_agent hey"), AsyncMock())

        assert "unknown agent 'missing_agent'" in result.result[0].content

    async def test_non_http_agent_returns_direct_error(self):
        registry = MagicMock()
        registry.find_by_name.return_value = {"name": "copilot", "transport": "acp", "url": None}
        mw = DirectA2AFastPathMiddleware(tool_registry=registry)

        result = await mw.awrap_model_call(self._request("@copilot hey"), AsyncMock())

        assert "not an HTTP A2A agent" in result.result[0].content

    async def test_empty_body_returns_direct_error(self):
        mw = DirectA2AFastPathMiddleware(tool_registry=MagicMock())

        result = await mw.awrap_model_call(self._request("@example_agent "), AsyncMock())

        assert "requires a request" in result.result[0].content

    async def test_blocking_invocation_passes_thread_metadata(self):
        registry = MagicMock()
        registry.find_by_name.return_value = {
            "name": "example_agent",
            "transport": "http",
            "url": "http://localhost:8109",
        }
        client = MagicMock()
        client.send_message = AsyncMock(
            return_value={"success": True, "result": {"status": {"message": {"text": "ok"}}}}
        )
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)

        await mw.awrap_model_call(
            self._request("@example_agent hello", state={"thread_id": "thread-direct"}),
            AsyncMock(),
        )

        kwargs = client.send_message.await_args.kwargs
        assert kwargs["blocking"] is True
        assert kwargs["context"]["thread_id"] == "thread-direct"
        assert kwargs["context"]["routing_mode"] == "direct_prefix"


class TestToolTimingMiddleware:
    """Tests for checkpointed tool-call timing."""

    async def test_stamps_duration_on_tool_message(self):
        middleware = ToolTimingMiddleware()
        handler = AsyncMock(return_value=ToolMessage("result", tool_call_id="tc_1"))
        request = MagicMock()
        request.tool_call = {"name": "current_time", "args": {}, "id": "tc_1"}

        result = await middleware.awrap_tool_call(request, handler)

        assert isinstance(result, ToolMessage)
        assert result.additional_kwargs["duration_ms"] >= 0

    async def test_preserves_tool_failure(self):
        middleware = ToolTimingMiddleware()
        handler = AsyncMock(side_effect=RuntimeError("command failed"))
        request = MagicMock()
        request.tool_call = {"name": "shell", "args": {}, "id": "tc_2"}

        with pytest.raises(RuntimeError, match="command failed"):
            await middleware.awrap_tool_call(request, handler)


class TestAsyncSubagentTrackingMiddleware:
    class FakeStateManager:
        def __init__(self):
            self.tasks = [
                AsyncSubagentTaskRecord(
                    task_id="thread_completed",
                    agent_name="hello-world-async",
                    assistant_id="hello-world-async",
                    agent_url="http://127.0.0.1:9501/agent-protocol",
                    run_id="run_completed",
                    status="success",
                    summary="HELLO_FROM_ASYNC_SUBAGENT",
                    latest_message="HELLO_FROM_ASYNC_SUBAGENT",
                )
            ]

        async def get_async_subagent_tasks(self):
            return self.tasks

        async def get_async_subagent_task(self, task_id):
            return next((task for task in self.tasks if task.task_id == task_id), None)

        async def update_async_subagent_task(self, task_id, updates):
            return True

    async def test_list_async_tasks_includes_persisted_history(self):
        mw = AsyncSubagentTrackingMiddleware(state_manager=self.FakeStateManager())
        request = MagicMock()
        request.tool_call = {"name": "list_async_tasks", "args": {}, "id": "tc_async"}
        handler = AsyncMock(
            return_value=ToolMessage(
                content='{"tasks":[],"total":0}',
                tool_call_id="tc_async",
            )
        )

        result = await mw.awrap_tool_call(request, handler)

        assert isinstance(result, ToolMessage)
        payload = json.loads(str(result.content))
        assert payload["total"] == 1
        assert payload["success"] == 1
        assert payload["tasks"][0]["task_id"] == "thread_completed"
        assert payload["tasks"][0]["summary"] == "HELLO_FROM_ASYNC_SUBAGENT"
