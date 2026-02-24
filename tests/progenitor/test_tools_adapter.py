"""Tests for progenitor-core DeepAgent tool adapters (core-only)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.agent.tools_adapter import create_deepagent_tools


def _tool_by_name(tools, name: str):
    return next(t for t in tools if t.name == name)


def test_core_only_tools_present() -> None:
    tools = create_deepagent_tools()
    names = {t.name for t in tools}
    assert names == {"say_to_user", "list_agents", "call_agent"}


@pytest.mark.asyncio
async def test_list_agents_returns_registry_entries() -> None:
    registry = MagicMock()
    registry.get_agents.return_value = [{"name": "Hello World Agent"}]
    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "list_agents")

    result = await t.ainvoke({"refresh": False})
    assert result["count"] == 1
    assert result["agents"][0]["name"] == "Hello World Agent"


@pytest.mark.asyncio
async def test_call_agent_uses_registry_and_a2a(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "transport": "http",
        "endpoint": "http://localhost:9999",
        "status": "running",
    }

    mock_a2a = MagicMock()
    mock_a2a.call_agent = AsyncMock(return_value={"success": True, "response": "ok"})
    monkeypatch.setattr("app.progenitor.agent.tools_adapter.A2AClient", lambda: mock_a2a)

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"agent": "Builder Agent", "request": "hello", "timeout_seconds": 1.0})
    assert result["success"] is True
    mock_a2a.call_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_agent_unknown_agent_returns_error() -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = None
    registry.find_agent_by_capability.return_value = None

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"agent": "does-not-exist", "request": "hello"})
    assert result["success"] is False
    assert "Unknown agent" in result["error"]
