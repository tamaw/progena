"""Tests for Progenitor DeepAgent tool adapters."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import StructuredTool

from src.progenitor.agent.adapters import create_deepagent_tools, create_dynamic_mcp_tools
from src.progenitor.config import MCPConfig
from src.progenitor.mcp.runtime import MCPRuntime
from src.progenitor.tools.a2a_sessions import A2ASendArgs
from src.progenitor.tools.protocol import RequestToolArgs


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
        "list_agents",
        "a2a_send",
        "a2a_send_async",
        "a2a_status",
        "a2a_cancel",
        "a2a_list",
        "acp_start",
        "acp_send",
        "acp_status",
        "acp_answer",
        "acp_cancel",
        "acp_list",
        "enqueue_background_task",
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
    assert _tool_by_name(tools, "a2a_send").args_schema is A2ASendArgs


def test_user_communication_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()

    say_schema = _tool_by_name(tools, "say_to_user").args_schema.model_json_schema()
    ask_schema = _tool_by_name(tools, "ask_user").args_schema.model_json_schema()

    assert "message" in say_schema.get("properties", {})
    assert say_schema.get("required") == ["message"]

    ask_properties = ask_schema.get("properties", {})
    assert {"question", "context", "options"} <= set(ask_properties)
    assert ask_schema.get("required") == ["question"]


def test_reflection_tool_exposes_structured_parameters() -> None:
    tools = create_deepagent_tools()

    reflect_schema = _tool_by_name(tools, "self_reflect").args_schema.model_json_schema()

    reflect_properties = reflect_schema.get("properties", {})
    assert {"operation", "file_path", "query", "topic"} <= set(reflect_properties)
    assert reflect_schema.get("required") == ["operation"]


def test_a2a_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()
    send_schema = _tool_by_name(tools, "a2a_send").args_schema.model_json_schema()
    status_schema = _tool_by_name(tools, "a2a_status").args_schema.model_json_schema()
    send_properties = send_schema.get("properties", {})
    status_properties = status_schema.get("properties", {})

    assert {"agent", "message", "task_id", "context_id", "wait_seconds", "context"} <= set(
        send_properties
    )
    assert send_schema.get("required") == ["agent", "message"]
    assert {"task_id", "wait_seconds"} <= set(status_properties)
    assert status_schema.get("required") == ["task_id"]


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
    from src.progenitor.tools.shell import ShellExecArgs

    tools = create_deepagent_tools()
    shell_tool = _tool_by_name(tools, "shell_exec")

    assert shell_tool.args_schema is ShellExecArgs

    captured: dict[str, Any] = {}

    async def _fake_execute(self, context):  # type: ignore[no-untyped-def]
        from src.progenitor.tools.protocol import ToolResult

        captured["user_input"] = context.user_input
        captured["tool_args"] = context.tool_args
        return ToolResult.ok({"ok": True})

    monkeypatch.setattr(
        "src.progenitor.tools.shell.ShellExecTool.execute", _fake_execute, raising=True
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
async def test_a2a_status_kwargs_populate_tool_args(monkeypatch) -> None:
    """The adapter passes structured status kwargs through as tool arguments."""
    tools = create_deepagent_tools(state_manager=MagicMock())
    tool = _tool_by_name(tools, "a2a_status")
    captured: dict[str, Any] = {}

    async def _fake_execute(self, context):  # type: ignore[no-untyped-def]
        from src.progenitor.tools.protocol import ToolResult

        captured["user_input"] = context.user_input
        captured["tool_args"] = context.tool_args
        return ToolResult.ok({"status": "ok"})

    monkeypatch.setattr(
        "src.progenitor.tools.a2a_sessions.A2AStatusTool.execute",
        _fake_execute,
        raising=True,
    )
    await _invoke(tool, {"task_id": "task-xyz", "wait_seconds": 7})
    assert captured["user_input"] == ""
    assert captured["tool_args"] == {"task_id": "task-xyz", "wait_seconds": 7}


@pytest.mark.asyncio
async def test_parallel_a2a_progress_stays_on_its_originating_tool_row(monkeypatch) -> None:
    """Real wrapped sends retain each call's UI progress correlation."""
    import asyncio
    from unittest.mock import AsyncMock

    events = []

    class Client:
        async def send_message(self, *, request, on_progress, **kwargs):
            await asyncio.sleep(0)
            await on_progress(
                {
                    "status": {
                        "state": "working",
                        "message": {"parts": [{"kind": "text", "text": request}]},
                    }
                }
            )
            return {
                "success": True,
                "result": {
                    "kind": "task",
                    "id": f"task-{request}",
                    "contextId": "context",
                    "status": {"state": "completed"},
                },
            }

    monkeypatch.setattr("src.progenitor.a2a.tasks.A2AClient", Client)
    monkeypatch.setattr("src.progenitor.tools.a2a_sessions.emit_stream_event", events.append)
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "name": "builder",
        "url": "https://example.test",
        "transport": "http",
    }
    state = AsyncMock()
    state.get_current_state.return_value = {"thread_id": "thread"}
    state.get_a2a_task.return_value = None
    tool = _tool_by_name(
        create_deepagent_tools(tool_registry=registry, state_manager=state), "a2a_send"
    )

    first, second = await asyncio.gather(
        _invoke(tool, {"agent": "builder", "message": "first"}, tool_call_id="call-first"),
        _invoke(tool, {"agent": "builder", "message": "second"}, tool_call_id="call-second"),
    )
    assert first["task_id"] == "task-first"
    assert second["task_id"] == "task-second"
    assert {(event["metadata"]["tool_call_id"], event["content"]) for event in events} == {
        ("call-first", "first"),
        ("call-second", "second"),
    }
