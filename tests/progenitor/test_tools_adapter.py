"""Tests for Progenitor DeepAgent tool adapters."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.agent.tools_adapter import create_deepagent_tools


def _tool_by_name(tools, name: str):
    return next(t for t in tools if t.name == name)


def test_core_only_tools_present() -> None:
    tools = create_deepagent_tools()
    names = {t.name for t in tools}
    assert {"say_to_user", "ask_user", "notify_user", "list_agents", "call_agent", "mcp_call", "mcp_refresh"}.issubset(names)
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
        "transport": "http",
        "endpoint": "http://localhost:9999",
        "status": "running",
    }

    mock_a2a = MagicMock()
    mock_a2a.get_agent_card = AsyncMock(return_value={"url": "http://localhost:9999"})
    mock_a2a.call_agent = AsyncMock(return_value={"success": True, "response": "ok"})
    monkeypatch.setattr("app.progenitor.tools.call_agent.A2AClient", lambda: mock_a2a)

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"tool_args": {"agent": "Builder Agent", "request": "hello", "timeout_seconds": 1.0}})
    assert result.get("success") is True
    mock_a2a.call_agent.assert_awaited_once()
    mock_a2a.get_agent_card.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_agent_uses_card_url_when_endpoint_missing(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "transport": "http",
        "status": "running",
    }

    captured_cfg: dict[str, str] = {}

    async def _call_agent(agent_config, request, context=None, timeout=60.0):  # noqa: ARG001
        captured_cfg.update(agent_config)
        return {"success": True, "response": "ok"}

    mock_a2a = MagicMock()
    mock_a2a.get_agent_card = AsyncMock(return_value={"url": "http://localhost:9100"})
    mock_a2a.call_agent = AsyncMock(side_effect=_call_agent)
    monkeypatch.setattr("app.progenitor.tools.call_agent.A2AClient", lambda: mock_a2a)

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"tool_args": {"agent": "Builder Agent", "request": "hello"}})
    assert result.get("success") is True
    assert captured_cfg.get("url") == "http://localhost:9100"


@pytest.mark.asyncio
async def test_call_agent_supports_url_only_registry_entry(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "transport": "http",
        "url": "http://localhost:9200",
    }

    captured_cfg: dict[str, str] = {}

    async def _call_agent(agent_config, request, context=None, timeout=60.0):  # noqa: ARG001
        captured_cfg.update(agent_config)
        return {"success": True, "response": "ok"}

    mock_a2a = MagicMock()
    mock_a2a.get_agent_card = AsyncMock(return_value=None)
    mock_a2a.call_agent = AsyncMock(side_effect=_call_agent)
    monkeypatch.setattr("app.progenitor.tools.call_agent.A2AClient", lambda: mock_a2a)

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"tool_args": {"agent": "Builder Agent", "request": "hello"}})
    assert result.get("success") is True
    assert captured_cfg.get("url") == "http://localhost:9200"


@pytest.mark.asyncio
async def test_call_agent_unknown_agent_returns_error() -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = None
    registry.find_agent_by_capability.return_value = None

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"tool_args": {"agent": "does-not-exist", "request": "hello"}})
    assert result.get("success") is False
    assert "Unknown agent" in str(result.get("error"))


@pytest.mark.asyncio
async def test_call_agent_accepts_agent_name_alias(monkeypatch) -> None:
    registry = MagicMock()
    registry.find_by_name.return_value = {
        "transport": "http",
        "url": "http://localhost:9200",
    }

    mock_a2a = MagicMock()
    mock_a2a.get_agent_card = AsyncMock(return_value=None)
    mock_a2a.call_agent = AsyncMock(return_value={"success": True, "response": "ok"})
    monkeypatch.setattr("app.progenitor.tools.call_agent.A2AClient", lambda: mock_a2a)

    tools = create_deepagent_tools(tool_registry=registry)
    t = _tool_by_name(tools, "call_agent")

    result = await t.ainvoke({"agent_name": "Builder Agent", "request": "hello"})
    assert result.get("success") is True
    mock_a2a.call_agent.assert_awaited_once()
