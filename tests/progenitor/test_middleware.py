"""
Tests for Phase 4: Custom middleware for DeepAgent.
"""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from app.progenitor.agent.middleware import (
    AsyncSubagentTrackingMiddleware,
    AuditLoggingMiddleware,
    DirectA2AFastPathMiddleware,
    DynamicMCPToolMiddleware,
    SecretRedactionMiddleware,
)
from app.progenitor.state.models import AsyncSubagentTaskRecord


class TestSecretRedactionMiddleware:
    """Tests for the secret redaction middleware."""

    def test_instantiation(self):
        mw = SecretRedactionMiddleware()
        assert mw is not None

    def test_sync_wrap_passes_through(self):
        """Sync wrap_tool_call just passes through."""
        mw = SecretRedactionMiddleware()
        handler = MagicMock(return_value="result")
        request = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == "result"

    async def test_async_wrap_passes_through(self):
        """Async awrap_tool_call passes through to handler."""
        mw = SecretRedactionMiddleware()
        handler = AsyncMock(return_value="result")
        request = MagicMock()
        request.tool_call = {
            "name": "shell",
            "args": {"command": "echo hello"},
            "id": "tc_1",
        }
        # awrap_tool_call returns the coroutine from handler
        await mw.awrap_tool_call(request, handler)
        # It should call handler with the original request (not redacted)
        handler.assert_called_once_with(request)


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
            "name": "greetings_agent",
            "transport": "http",
            "url": "http://localhost:8109",
        }
        client = MagicMock()
        client.call_agent = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "status": {
                        "message": {
                            "parts": [{"root": {"text": "Hello from greetings_agent!"}}],
                            "metadata": {"display_agent_name": "greetings_agent"},
                        }
                    }
                },
            }
        )
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)

        result = await mw.awrap_model_call(self._request("@greetings_agent hey"), AsyncMock())

        client.call_agent.assert_awaited_once()
        assert result.result[0].content == "Hello from greetings_agent!"
        assert result.result[0].response_metadata["display_agent_name"] == "greetings_agent"

    async def test_prefix_only_matches_at_start_of_latest_user_message(self):
        registry = MagicMock()
        client = MagicMock()
        client.call_agent = AsyncMock()
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)
        handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="llm")]))

        result = await mw.awrap_model_call(self._request("hello @greetings_agent hey"), handler)

        handler.assert_awaited_once()
        client.call_agent.assert_not_called()
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

        result = await mw.awrap_model_call(self._request("@greetings_agent "), AsyncMock())

        assert "requires a request" in result.result[0].content

    async def test_blocking_invocation_passes_thread_metadata(self):
        registry = MagicMock()
        registry.find_by_name.return_value = {
            "name": "greetings_agent",
            "transport": "http",
            "url": "http://localhost:8109",
        }
        client = MagicMock()
        client.call_agent = AsyncMock(
            return_value={"success": True, "result": {"status": {"message": {"text": "ok"}}}}
        )
        mw = DirectA2AFastPathMiddleware(tool_registry=registry, client_factory=lambda: client)

        await mw.awrap_model_call(
            self._request("@greetings_agent hello", state={"thread_id": "thread-direct"}),
            AsyncMock(),
        )

        kwargs = client.call_agent.await_args.kwargs
        assert kwargs["blocking"] is True
        assert kwargs["context"]["thread_id"] == "thread-direct"
        assert kwargs["context"]["routing_mode"] == "direct_prefix"


class TestAuditLoggingMiddleware:
    """Tests for the audit logging middleware."""

    class FakeSink:
        def __init__(self):
            self.entries: list[dict[str, Any]] = []

        async def write_log(self, **kwargs):
            self.entries.append(kwargs)

    def test_instantiation_with_sink(self):
        sink = self.FakeSink()
        mw = AuditLoggingMiddleware(log_sink=sink)
        assert mw._log_sink is sink

    async def test_logs_successful_call(self):
        """Logs a successful tool call with timing."""
        sink = self.FakeSink()
        mw = AuditLoggingMiddleware(log_sink=sink)
        handler = AsyncMock(return_value="tool_result")
        request = MagicMock()
        request.tool_call = {
            "name": "current_time",
            "args": {},
            "id": "tc_1",
        }

        result = await mw.awrap_tool_call(request, handler)
        assert result == "tool_result"

        entry = sink.entries[0]["payload"]
        assert entry["tool_name"] == "current_time"
        assert entry["success"] is True
        assert entry["error"] is None
        assert "duration_ms" in entry
        assert "timestamp" in entry

    async def test_logs_failed_call(self):
        """Logs a failed tool call with error."""
        sink = self.FakeSink()
        mw = AuditLoggingMiddleware(log_sink=sink)
        handler = AsyncMock(side_effect=RuntimeError("command failed"))
        request = MagicMock()
        request.tool_call = {
            "name": "shell",
            "args": {"command": "false"},
            "id": "tc_2",
        }

        with pytest.raises(RuntimeError, match="command failed"):
            await mw.awrap_tool_call(request, handler)

        entry = sink.entries[0]["payload"]
        assert entry["tool_name"] == "shell"
        assert entry["success"] is False
        assert "command failed" in entry["error"]

    async def test_redacts_secrets_in_log(self):
        """Secrets in args are redacted in the log."""
        sink = self.FakeSink()
        mw = AuditLoggingMiddleware(log_sink=sink)
        handler = AsyncMock(return_value="ok")
        request = MagicMock()
        request.tool_call = {
            "name": "shell",
            "args": {"command": "export OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz12345"},
            "id": "tc_3",
        }

        await mw.awrap_tool_call(request, handler)

        entry = sink.entries[0]["payload"]
        assert "sk-proj-" not in entry["args"]["command"]
        assert "REDACTED" in entry["args"]["command"]

    def test_sync_wrap_passes_through(self):
        mw = AuditLoggingMiddleware()
        handler = MagicMock(return_value="result")
        request = MagicMock()
        result = mw.wrap_tool_call(request, handler)
        handler.assert_called_once_with(request)
        assert result == "result"


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


class TestMiddlewareIntegration:
    """Integration test: middleware wired into factory."""

    async def test_factory_accepts_middleware_param(self):
        """create_progenitor_agent accepts log_sink param."""
        from types import SimpleNamespace
        from unittest.mock import patch

        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.agent import create_progenitor_agent

                class _FakeStateManager:
                    def __init__(self):
                        self._docs = {
                            "prompt": {"progenitor/genesis.md": "Genesis"},
                            "skill": {},
                            "agent": {},
                            "memory": {},
                        }

                    async def get_content_document(self, kind, relative_path):
                        return self._docs[kind].get(relative_path)

                    async def save_content_document(self, kind, relative_path, content):
                        self._docs[kind][relative_path] = content

                    async def delete_content_document(self, kind, relative_path):
                        self._docs[kind].pop(relative_path, None)

                    async def list_content_documents(self, kind):
                        return list(self._docs[kind].items())

                    async def list_routine_graphs(self):
                        return []

                with tempfile.TemporaryDirectory() as tmpdir:
                    prompts_dir = Path(tmpdir) / "prompts"
                    prompts_dir.mkdir()
                    fake_config = SimpleNamespace(
                        llm=SimpleNamespace(model_default="gpt-chat-latest"),
                        storage=SimpleNamespace(
                            prompts_dir=prompts_dir,
                            shim_bin_dir=Path(tmpdir) / "shims" / "bin",
                            shell_home_dir=Path(tmpdir) / "shell-home",
                        ),
                        runtime=SimpleNamespace(shell_timeout_seconds=120),
                    )
                    await create_progenitor_agent(
                        config=fake_config,
                        checkpointer=MagicMock(),
                        store=MagicMock(),
                        state_manager=_FakeStateManager(),
                        skills_dirs=[],
                        memory_paths=[],
                        log_sink=MagicMock(),
                    )

            # Verify middleware was passed
            call_kwargs = mock_create.call_args.kwargs
            assert "middleware" in call_kwargs
            middleware = call_kwargs["middleware"]
            types = [type(m).__name__ for m in middleware]
            assert "DynamicMCPToolMiddleware" in types
            assert "SecretRedactionMiddleware" in types
            assert "AuditLoggingMiddleware" in types
