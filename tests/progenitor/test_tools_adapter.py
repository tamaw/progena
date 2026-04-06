"""Tests for Progenitor DeepAgent tool adapters."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.agent.tools_adapter import create_deepagent_tools
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
        "mcp_call",
        "mcp_refresh",
    }.issubset(names)
    # System tools are dynamically discovered from app/progenitor/tools/.
    assert {"current_time", "list_tools"}.issubset(names)
    assert len(names) == len(tools)


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

    result = await t.ainvoke(
        {"tool_args": {"agent": "Builder Agent", "request": "hello", "timeout_seconds": 1.0}}
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

    result = await t.ainvoke({"tool_args": {"agent": "Builder Agent", "request": "hello"}})
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

    result = await t.ainvoke({"tool_args": {"agent": "Builder Agent", "request": "hello"}})
    assert result["task_id"] == "task-123"
    assert dispatch_mock.await_args.kwargs["agent_config"]["url"] == "http://localhost:9200"


@pytest.mark.asyncio
async def test_call_agent_unknown_agent_returns_error() -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = None
    registry.find_agent_by_capability.return_value = None

    tools = create_deepagent_tools(tool_registry=registry, state_manager=MagicMock())
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"tool_args": {"agent": "does-not-exist", "request": "hello"}})
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
