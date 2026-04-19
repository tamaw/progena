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


def test_request_only_tools_keep_default_request_schema() -> None:
    tools = create_deepagent_tools()
    tool = _tool_by_name(tools, "current_time")
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})

    assert set(properties) == {"request"}
    assert schema.get("required") == ["request"]


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


def test_schedule_and_reflection_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()

    schedule_schema = _tool_by_name(tools, "schedule_task").args_schema.model_json_schema()
    delete_schema = _tool_by_name(tools, "delete_schedule").args_schema.model_json_schema()
    reflect_schema = _tool_by_name(tools, "self_reflect").args_schema.model_json_schema()

    assert {"user_request", "time_expression", "instruction"} <= set(
        schedule_schema.get("properties", {})
    )
    assert schedule_schema.get("required") == ["user_request", "time_expression", "instruction"]

    assert "schedule_id" in delete_schema.get("properties", {})
    assert delete_schema.get("required") == ["schedule_id"]

    reflect_properties = reflect_schema.get("properties", {})
    assert {"operation", "file_path", "query", "topic"} <= set(reflect_properties)
    assert reflect_schema.get("required") == ["operation"]


def test_call_agent_and_task_tools_expose_structured_parameters() -> None:
    tools = create_deepagent_tools()

    call_schema = _tool_by_name(tools, "call_agent").args_schema.model_json_schema()
    task_schema = _tool_by_name(tools, "get_a2a_task").args_schema.model_json_schema()
    refresh_schema = _tool_by_name(tools, "mcp_refresh").args_schema.model_json_schema()

    call_properties = call_schema.get("properties", {})
    assert {"request", "agent", "agent_name", "timeout_seconds", "blocking", "context"} <= set(
        call_properties
    )
    assert call_schema.get("required") == ["request"]

    task_properties = task_schema.get("properties", {})
    assert {"task_id", "refresh", "history_length"} <= set(task_properties)
    assert task_schema.get("required") == ["task_id"]

    assert "persist" in refresh_schema.get("properties", {})
    assert "required" not in refresh_schema or refresh_schema.get("required") == []


@pytest.mark.asyncio
async def test_list_agents_returns_registry_entries() -> None:
    registry = MagicMock()
    registry.get_agents.return_value = [{"name": "Hello World Agent"}]
    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "list_agents")

    result = await t.ainvoke({"request": "list agents"})
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
async def test_call_agent_accepts_agent_name_alias(monkeypatch) -> None:
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

    result = await t.ainvoke({"agent_name": "Builder Agent", "request": "hello"})
    assert result["task_id"] == "task-123"
    dispatch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_task_accepts_structured_arguments_without_llm_extraction() -> None:
    state_manager = AsyncMock()
    state_manager.get_current_state = AsyncMock(return_value={"schedule": []})
    registry = MagicMock()
    tools = create_deepagent_tools(tool_registry=registry, state_manager=state_manager)
    t = _tool_by_name(tools, "schedule_task")

    result = await t.ainvoke(
        {
            "user_request": "Remind me to stretch",
            "time_expression": "in 5 minutes",
            "instruction": "Tell the user to stretch.",
            "description": "Stretch reminder",
        }
    )

    assert result["success"] is True
    assert result["schedule_id"].startswith("sched_")
    state_manager.add_schedule_entry.assert_awaited_once()


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
