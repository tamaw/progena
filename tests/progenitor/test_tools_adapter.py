"""Tests for Progenitor DeepAgent tool adapters."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import StructuredTool

from app.progenitor.agent.tools_adapter import create_deepagent_tools, create_dynamic_mcp_tools
from app.progenitor.state.models import A2ATaskRecord


def _tool_by_name(tools, name: str):
    return next(t for t in tools if t.name == name)


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
        "get_a2a_task",
        "mcp_refresh",
    }.issubset(names)
    assert {"current_time", "list_tools"}.issubset(names)
    assert len(names) == len(tools)


def test_current_system_tools_do_not_expose_generic_tool_args_envelope() -> None:
    tools = create_deepagent_tools()

    for tool in tools:
        if tool.args_schema is None:
            continue
        properties = tool.args_schema.model_json_schema().get("properties", {})
        assert "tool_args" not in properties, tool.name


def test_user_communication_tools_expose_request_contract() -> None:
    tools = create_deepagent_tools()

    say_tool = _tool_by_name(tools, "say_to_user")
    say_props = say_tool.args_schema.model_json_schema().get("properties", {})
    assert "message" in say_props
    assert "request" not in say_props
    assert "tool_args" not in say_props

    ask_tool = _tool_by_name(tools, "ask_user")
    ask_props = ask_tool.args_schema.model_json_schema().get("properties", {})
    assert {"question", "options"}.issubset(ask_props)
    assert "request" not in ask_props

    notify_tool = _tool_by_name(tools, "notify_user")
    notify_props = notify_tool.args_schema.model_json_schema().get("properties", {})
    assert {"title", "body", "level"}.issubset(notify_props)
    assert "request" not in notify_props


def test_call_agent_exposes_explicit_contract() -> None:
    tools = create_deepagent_tools()
    tool = _tool_by_name(tools, "call_agent")
    properties = tool.args_schema.model_json_schema().get("properties", {})

    assert {"agent", "request", "timeout_seconds", "blocking", "context"}.issubset(properties)
    assert "agent_name" not in properties
    assert "tool_args" not in properties


def test_remaining_adapter_tools_expose_explicit_contracts() -> None:
    tools = create_deepagent_tools()

    schedule_task = _tool_by_name(tools, "schedule_task")
    schedule_props = schedule_task.args_schema.model_json_schema().get("properties", {})
    assert {"user_request"}.issubset(schedule_props)
    assert "request" not in schedule_props

    shell_exec = _tool_by_name(tools, "shell_exec")
    shell_props = shell_exec.args_schema.model_json_schema().get("properties", {})
    assert {"command", "timeout_seconds", "cwd"}.issubset(shell_props)

    gh_cli = _tool_by_name(tools, "gh_cli")
    gh_props = gh_cli.args_schema.model_json_schema().get("properties", {})
    assert {"args", "timeout", "cwd"}.issubset(gh_props)

    semantic_registry = _tool_by_name(tools, "semantic_registry")
    semantic_props = semantic_registry.args_schema.model_json_schema().get("properties", {})
    assert {"query"}.issubset(semantic_props)


@pytest.mark.asyncio
async def test_list_agents_returns_registry_entries() -> None:
    registry = MagicMock()
    registry.get_agents.return_value = [{"name": "Hello World Agent"}]
    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "list_agents")

    result = await t.ainvoke({})
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

    result = await t.ainvoke({"agent": "Builder Agent", "request": "hello", "timeout_seconds": 1.0})

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

    result = await t.ainvoke({"agent": "Builder Agent", "request": "hello"})
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

    result = await t.ainvoke({"agent": "Builder Agent", "request": "hello"})
    assert result["task_id"] == "task-123"
    assert dispatch_mock.await_args.kwargs["agent_config"]["url"] == "http://localhost:9200"


@pytest.mark.asyncio
async def test_call_agent_unknown_agent_returns_error() -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = None
    registry.find_agent_by_capability.return_value = None

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"agent": "does-not-exist", "request": "hello"})
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

    result = await t.ainvoke({"request": "hello"})
    assert result["task_id"] == "task-123"
    dispatch_mock.assert_awaited_once()
    llm_client.chat_structured.assert_awaited_once()


@pytest.mark.asyncio
async def test_dynamic_mcp_tools_return_live_adapter_snapshot() -> None:
    llm_client = MagicMock()
    llm_client.mcp_langchain_tools = [_mcp_tool("search_web")]

    tools = create_dynamic_mcp_tools(llm_client)
    tool = _tool_by_name(tools, "search_web")

    result = await tool.ainvoke({"query": "langgraph"})

    assert result["ok"] is True
    assert result["query"] == "langgraph"


def test_dynamic_mcp_tools_skip_non_tool_entries() -> None:
    llm_client = MagicMock()
    llm_client.mcp_langchain_tools = [{"name": "not-a-tool"}, _mcp_tool("read_url")]

    tools = create_dynamic_mcp_tools(llm_client)

    assert [tool.name for tool in tools] == ["read_url"]
