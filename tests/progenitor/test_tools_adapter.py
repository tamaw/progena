"""Tests for Progenitor DeepAgent tool adapters."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from app.progenitor.agent.adapters import create_deepagent_tools, create_dynamic_mcp_tools
from app.progenitor.config import MCPConfig
from app.progenitor.mcp import MCPRuntime
from app.progenitor.state.models import A2ATaskRecord
from app.progenitor.tools.call_agent import CallAgentArgs
from app.progenitor.tools.protocol import RequestToolArgs


def _tool_by_name(tools, name: str):
    return next(t for t in tools if t.name == name)


async def _invoke(tool, args: dict[str, Any], *, tool_call_id: str = "call_test"):
    """Invoke a wrapped system tool the way LangGraph's real ToolNode does --
    a full ToolCall dict, not a plain args dict. Every wrapped tool now
    carries an InjectedToolCallId parameter (see adapters.py), and LangChain
    requires the full ToolCall shape whenever that's present. Unwraps the
    resulting ToolMessage back into a plain dict so callers can keep
    asserting on it directly."""
    import json

    message = await tool.ainvoke(
        {"name": tool.name, "args": args, "id": tool_call_id, "type": "tool_call"}
    )
    return json.loads(message.content)


def _tracked_task(agent_name: str = "Builder Agent") -> A2ATaskRecord:
    return A2ATaskRecord(
        task_id="task-123",
        agent_name=agent_name,
        agent_url="http://localhost:9999",
        context_id="ctx-123",
        source="call_agent",
        request_text="hello",
    )


def _mcp_tool(name: str) -> StructuredTool:
    async def _run(query: str = "", url: str = "") -> dict[str, str | bool]:
        result: dict[str, str | bool] = {"ok": True}
        if query:
            result["query"] = query
        if url:
            result["url"] = url
        return result

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=f"Tool: {name}",
    )


def test_core_only_tools_present() -> None:
    tools = create_deepagent_tools()
    names = {t.name for t in tools}
    assert {
        "say_to_user",
        "ask_user",
        "notify_user",
        "list_agents",
        "call_agent",
        "enqueue_background_task",
        "get_a2a_task",
        "mcp_refresh",
    }.issubset(names)
    assert {"current_time", "list_tools"}.issubset(names)
    assert len(names) == len(tools)


def test_request_only_tools_keep_default_request_schema() -> None:
    tools = create_deepagent_tools()
    tool = _tool_by_name(tools, "current_time")
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})

    assert set(properties) == {"request"}
    assert schema.get("required") == ["request"]


def test_deepagent_reuses_declared_args_models() -> None:
    tools = create_deepagent_tools()

    assert _tool_by_name(tools, "current_time").args_schema is RequestToolArgs
    assert _tool_by_name(tools, "call_agent").args_schema is CallAgentArgs


def test_user_communication_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()

    say_schema = _tool_by_name(tools, "say_to_user").args_schema.model_json_schema()
    ask_schema = _tool_by_name(tools, "ask_user").args_schema.model_json_schema()
    notify_schema = _tool_by_name(tools, "notify_user").args_schema.model_json_schema()

    assert "message" in say_schema.get("properties", {})
    assert say_schema.get("required") == ["message"]

    ask_properties = ask_schema.get("properties", {})
    assert {"question", "context", "options"} <= set(ask_properties)
    assert ask_schema.get("required") == ["question"]

    notify_properties = notify_schema.get("properties", {})
    assert {"title", "body", "level"} <= set(notify_properties)
    assert notify_schema.get("required") == ["title", "body"]


def test_reflection_tool_exposes_structured_parameters() -> None:
    tools = create_deepagent_tools()

    reflect_schema = _tool_by_name(tools, "self_reflect").args_schema.model_json_schema()

    reflect_properties = reflect_schema.get("properties", {})
    assert {"operation", "file_path", "query", "topic"} <= set(reflect_properties)
    assert reflect_schema.get("required") == ["operation"]


def test_call_agent_and_task_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()

    call_schema = _tool_by_name(tools, "call_agent").args_schema.model_json_schema()
    task_schema = _tool_by_name(tools, "get_a2a_task").args_schema.model_json_schema()
    background_schema = _tool_by_name(
        tools, "enqueue_background_task"
    ).args_schema.model_json_schema()

    call_properties = call_schema.get("properties", {})
    assert {"request", "agent", "agent_name", "timeout_seconds", "blocking", "context"} <= set(
        call_properties
    )
    assert call_schema.get("required") == ["request"]

    task_properties = task_schema.get("properties", {})
    assert {"task_id", "refresh", "history_length"} <= set(task_properties)
    assert task_schema.get("required") == ["task_id"]

    background_properties = background_schema.get("properties", {})
    assert {"request", "notify_on_completion", "isolated_thread", "completion_event_type"} <= set(
        background_properties
    )
    assert background_schema.get("required") == ["request"]


@pytest.mark.asyncio
async def test_list_agents_returns_registry_entries() -> None:
    registry = MagicMock()
    registry.get_agents.return_value = [{"name": "Hello World Agent"}]
    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "list_agents")

    result = await _invoke(t, {"request": "list agents"})
    assert result["total_count"] == 1
    assert result["agents"][0]["name"] == "Hello World Agent"


@pytest.mark.asyncio
async def test_call_agent_uses_registry_and_a2a(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "name": "Builder Agent",
        "transport": "http",
        "url": "http://localhost:9999",
        "status": "running",
    }

    dispatch_mock = AsyncMock(
        return_value=(
            {"success": True, "result": {"id": "task-123"}},
            _tracked_task(),
        )
    )
    monkeypatch.setattr("app.progenitor.tools.call_agent.dispatch_tracked_a2a_task", dispatch_mock)

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await _invoke(
        t, {"agent": "Builder Agent", "request": "hello", "timeout_seconds": 1.0}
    )

    assert result["task_id"] == "task-123"
    assert result["agent"] == "Builder Agent"
    dispatch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_agent_uses_card_url_when_endpoint_missing(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "name": "Builder Agent",
        "transport": "http",
        "status": "running",
    }

    dispatch_mock = AsyncMock(
        return_value=(
            {"success": True, "result": {"id": "task-123"}},
            _tracked_task(),
        )
    )
    monkeypatch.setattr("app.progenitor.tools.call_agent.dispatch_tracked_a2a_task", dispatch_mock)

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await _invoke(t, {"agent": "Builder Agent", "request": "hello"})
    assert result["task_id"] == "task-123"
    assert dispatch_mock.await_args.kwargs["agent_config"]["status"] == "running"


@pytest.mark.asyncio
async def test_call_agent_supports_url_only_registry_entry(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "name": "Builder Agent",
        "transport": "http",
        "url": "http://localhost:9200",
    }

    dispatch_mock = AsyncMock(
        return_value=(
            {"success": True, "result": {"id": "task-123"}},
            _tracked_task(),
        )
    )
    monkeypatch.setattr("app.progenitor.tools.call_agent.dispatch_tracked_a2a_task", dispatch_mock)

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await _invoke(t, {"agent": "Builder Agent", "request": "hello"})
    assert result["task_id"] == "task-123"
    assert dispatch_mock.await_args.kwargs["agent_config"]["url"] == "http://localhost:9200"


@pytest.mark.asyncio
async def test_call_agent_unknown_agent_returns_error() -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = None
    registry.find_agent_by_capability.return_value = None

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await _invoke(t, {"agent": "does-not-exist", "request": "hello"})
    assert result.get("success") is False
    assert "Unknown agent" in str(result.get("error"))


@pytest.mark.asyncio
async def test_call_agent_without_agent_routes_via_tool_llm(monkeypatch) -> None:
    registry = MagicMock()
    registry.get_agents.return_value = [
        {
            "name": "Builder Agent",
            "transport": "http",
            "url": "http://localhost:9200",
            "description": "Handles builds",
            "capabilities": ["build"],
        }
    ]
    registry.find_by_name.return_value = {
        "name": "Builder Agent",
        "transport": "http",
        "url": "http://localhost:9200",
    }

    llm_client = MagicMock()
    llm_client.chat_structured = AsyncMock(
        return_value=MagicMock(
            agent_name="Builder Agent",
            message="hello",
            reasoning="Builder Agent matches the task.",
        )
    )

    dispatch_mock = AsyncMock(
        return_value=(
            {"success": True, "result": {"id": "task-123"}},
            _tracked_task(),
        )
    )
    monkeypatch.setattr("app.progenitor.tools.call_agent.dispatch_tracked_a2a_task", dispatch_mock)

    tools = create_deepagent_tools(
        tool_registry=registry,
        llm_client=llm_client,
        state_manager=MagicMock(),
    )
    t = _tool_by_name(tools, "call_agent")

    result = await _invoke(t, {"request": "hello"})
    assert result["task_id"] == "task-123"
    dispatch_mock.assert_awaited_once()
    llm_client.chat_structured.assert_awaited_once()


@pytest.mark.asyncio
async def test_dynamic_mcp_tools_return_live_adapter_snapshot() -> None:
    llm_client = MagicMock()
    llm_client.mcp_runtime = MCPRuntime(MCPConfig(servers=[]))
    llm_client.mcp_runtime.set_context({}, [], [_mcp_tool("search_web")])

    tools = create_dynamic_mcp_tools(llm_client)
    tool = _tool_by_name(tools, "search_web")

    result = await tool.ainvoke({"query": "langgraph"})

    assert result["ok"] is True
    assert result["query"] == "langgraph"


def test_dynamic_mcp_tools_skip_non_tool_entries() -> None:
    llm_client = MagicMock()
    llm_client.mcp_runtime = MCPRuntime(MCPConfig(servers=[]))
    llm_client.mcp_runtime.set_context({}, [], [{"name": "not-a-tool"}, _mcp_tool("read_url")])

    tools = create_dynamic_mcp_tools(llm_client)

    assert [tool.name for tool in tools] == ["read_url"]


@pytest.mark.asyncio
async def test_shell_exec_receives_structured_kwargs(monkeypatch) -> None:
    """ShellExecTool exposes its structured args_model and execute() reads
    command/timeout/cwd directly from context.tool_args."""
    from app.progenitor.tools.shell import ShellExecArgs

    tools = create_deepagent_tools()
    shell_tool = _tool_by_name(tools, "shell_exec")

    assert shell_tool.args_schema is ShellExecArgs

    captured: dict[str, Any] = {}

    async def _fake_execute(self, context):  # type: ignore[no-untyped-def]
        from app.progenitor.tools.protocol import ToolResult

        captured["user_input"] = context.user_input
        captured["tool_args"] = context.tool_args
        return ToolResult.ok({"ok": True})

    monkeypatch.setattr(
        "app.progenitor.tools.shell.ShellExecTool.execute", _fake_execute, raising=True
    )

    result = await _invoke(
        shell_tool, {"command": "ls -la", "timeout_seconds": 60.0, "cwd": "/tmp"}
    )
    assert result == {"ok": True}
    assert captured["user_input"] == ""
    assert captured["tool_args"] == {
        "command": "ls -la",
        "timeout_seconds": 60.0,
        "cwd": "/tmp",
    }


@pytest.mark.asyncio
async def test_get_a2a_task_kwargs_populate_tool_args(monkeypatch) -> None:
    """The adapter passes structured kwargs through as context.tool_args."""
    tools = create_deepagent_tools(state_manager=MagicMock())
    tool = _tool_by_name(tools, "get_a2a_task")

    captured: dict[str, Any] = {}

    async def _fake_execute(self, context):  # type: ignore[no-untyped-def]
        from app.progenitor.tools.protocol import ToolResult

        captured["user_input"] = context.user_input
        captured["tool_args"] = context.tool_args
        return ToolResult.ok({"status": "ok"})

    monkeypatch.setattr(
        "app.progenitor.tools.get_a2a_task.GetA2ATaskTool.execute",
        _fake_execute,
        raising=True,
    )

    await _invoke(tool, {"task_id": "task-xyz", "refresh": False, "history_length": 7})

    assert captured["user_input"] == ""
    assert captured["tool_args"] == {
        "task_id": "task-xyz",
        "history_length": 7,
        "refresh": False,
    }


@pytest.mark.asyncio
async def test_tool_call_id_reaches_tool_context(monkeypatch) -> None:
    """Every wrapped system tool now carries an InjectedToolCallId parameter
    (see adapters.py) so it can tag its own live-progress stream events (e.g.
    call_agent's a2a_call_progress). Confirms the real id LangGraph's ToolNode
    supplies at invocation time actually reaches ToolContext.tool_call_id --
    this is the regression test for the empirical check done while building
    that wiring."""
    tools = create_deepagent_tools(state_manager=MagicMock())
    tool = _tool_by_name(tools, "get_a2a_task")

    captured: dict[str, Any] = {}

    async def _fake_execute(self, context):  # type: ignore[no-untyped-def]
        from app.progenitor.tools.protocol import ToolResult

        captured["tool_call_id"] = context.tool_call_id
        return ToolResult.ok({"status": "ok"})

    monkeypatch.setattr(
        "app.progenitor.tools.get_a2a_task.GetA2ATaskTool.execute",
        _fake_execute,
        raising=True,
    )

    await _invoke(tool, {"task_id": "task-xyz"}, tool_call_id="call_specific_id_789")

    assert captured["tool_call_id"] == "call_specific_id_789"
