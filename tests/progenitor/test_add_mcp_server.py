"""Tests for the add_mcp_server system tool."""

from __future__ import annotations

import copy
from typing import Any

import app.progenitor.tools.add_mcp_server as add_mcp_server_module
from app.progenitor.tools.add_mcp_server import AddMCPServerTool
from app.progenitor.tools.protocol import ToolContext


class _FakeStateManager:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload: dict[str, Any] = payload or {"mcpServers": {}}
        self.saved: list[dict[str, Any]] = []

    async def get_mcp_config(self) -> dict[str, Any]:
        return copy.deepcopy(self.payload)

    async def save_mcp_config(self, payload: dict[str, Any]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))


class _FakeRuntime:
    def __init__(self) -> None:
        self.mcp_tools = [{"name": "alpha"}, {"name": "beta"}]


class _FakeLLMClient:
    def __init__(self) -> None:
        self.mcp_runtime = _FakeRuntime()


def _patch_reload(monkeypatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_reload(
        llm_client, tool_registry, mcp_config, *, state_manager=None, reload_config=False
    ):
        calls.append({"reload_config": reload_config, "state_manager": state_manager})

    monkeypatch.setattr(add_mcp_server_module, "reload_mcp_runtime_context", fake_reload)
    return calls


async def test_add_mcp_server_persists_and_reloads(monkeypatch):
    calls = _patch_reload(monkeypatch)
    state_manager = _FakeStateManager()
    tool = AddMCPServerTool()
    context = ToolContext(
        tool_registry=object(),
        llm_client=_FakeLLMClient(),
        state_manager=state_manager,
        tool_args={
            "name": "jina",
            "url": "https://mcp.example.test/mcp",
            "description": "Web tools",
            "headers": {"Authorization": "Bearer $JINA_TOKEN"},
        },
    )

    result = await tool.execute(context)

    assert result.success
    assert result.data["added"] is True
    assert result.data["updated"] is False
    assert result.data["reloaded"] is True
    assert result.data["tool_count"] == 2

    server = state_manager.payload["mcpServers"]["jina"]
    assert server["url"] == "https://mcp.example.test/mcp"
    assert server["transport"] == "http"
    assert server["enabled"] is True
    assert server["description"] == "Web tools"
    assert server["headers"] == {"Authorization": "Bearer $JINA_TOKEN"}

    assert calls == [{"reload_config": True, "state_manager": state_manager}]


async def test_add_mcp_server_updates_existing(monkeypatch):
    _patch_reload(monkeypatch)
    state_manager = _FakeStateManager(
        {"mcpServers": {"jina": {"url": "https://old.test/mcp", "transport": "http"}}}
    )
    tool = AddMCPServerTool()
    context = ToolContext(
        tool_registry=object(),
        llm_client=_FakeLLMClient(),
        state_manager=state_manager,
        tool_args={"name": "jina", "url": "https://new.test/mcp"},
    )

    result = await tool.execute(context)

    assert result.success
    assert result.data["updated"] is True
    assert state_manager.payload["mcpServers"]["jina"]["url"] == "https://new.test/mcp"


async def test_add_mcp_server_requires_url(monkeypatch):
    _patch_reload(monkeypatch)
    state_manager = _FakeStateManager()
    tool = AddMCPServerTool()
    context = ToolContext(
        tool_registry=object(),
        llm_client=_FakeLLMClient(),
        state_manager=state_manager,
        tool_args={"name": "jina"},
    )

    result = await tool.execute(context)

    assert not result.success
    assert "url" in (result.error or "").lower()
    assert state_manager.saved == []


async def test_add_mcp_server_rejects_non_http_url(monkeypatch):
    _patch_reload(monkeypatch)
    state_manager = _FakeStateManager()
    tool = AddMCPServerTool()
    context = ToolContext(
        tool_registry=object(),
        llm_client=_FakeLLMClient(),
        state_manager=state_manager,
        tool_args={"name": "jina", "url": "ftp://nope"},
    )

    result = await tool.execute(context)

    assert not result.success
    assert "invalid url" in (result.error or "").lower()
    assert state_manager.saved == []


async def test_add_mcp_server_reports_saved_when_reload_fails(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("discovery exploded")

    monkeypatch.setattr(add_mcp_server_module, "reload_mcp_runtime_context", boom)
    state_manager = _FakeStateManager()
    tool = AddMCPServerTool()
    context = ToolContext(
        tool_registry=object(),
        llm_client=_FakeLLMClient(),
        state_manager=state_manager,
        tool_args={"name": "jina", "url": "https://mcp.example.test/mcp"},
    )

    result = await tool.execute(context)

    # The server is persisted even if live discovery fails; the result says so.
    assert result.success
    assert result.data["reloaded"] is False
    assert "jina" in state_manager.payload["mcpServers"]
