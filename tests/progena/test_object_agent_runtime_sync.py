"""Listener routing and coalescing without a database connection."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.progena.runtime.agent_sync import AgentSyncScheduler
from src.progena.runtime.progena import Progena


def runtime():
    instance = object.__new__(Progena)
    instance.state_manager = SimpleNamespace()
    instance._agent_scheduler = Mock()
    instance._mcp_scheduler = Mock()
    instance._plugin_scheduler = Mock()
    return instance


@pytest.mark.parametrize(
    "key",
    [
        "agent:user/helper/SYSTEM.md",
        "agent:native/helper/SYSTEM.md",
        "agent:market__plugin/helper/SYSTEM.md",
    ],
)
def test_listener_routes_object_documents(key):
    instance = runtime()
    instance._on_store_change(json.dumps({"key": key}))
    instance._agent_scheduler.request.assert_called_once_with()
    instance._mcp_scheduler.request.assert_not_called()
    instance._plugin_scheduler.request.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "garbage",
        "[]",
        '{"key": 1}',
        '{"key":"prompt:genesis.md"}',
        '{"key":"repos"}',
        '{"key":"repo_scan"}',
        '{"key":"background_tasks"}',
    ],
)
def test_listener_ignores_non_chat_and_malformed_notifications(payload):
    instance = runtime()
    instance._on_store_change(payload)
    instance._agent_scheduler.request.assert_not_called()
    instance._mcp_scheduler.request.assert_not_called()
    instance._plugin_scheduler.request.assert_not_called()


@pytest.mark.parametrize(("key", "full"), [("mcp", True), ("mcp_tools", False)])
def test_listener_routes_mcp_changes(key, full):
    instance = runtime()
    instance._on_store_change(json.dumps({"key": key}))
    instance._mcp_scheduler.request.assert_called_once_with(full=full)
    instance._agent_scheduler.request.assert_not_called()


def test_reconnect_requests_all_refreshes():
    instance = runtime()
    instance._on_store_resync()
    instance._agent_scheduler.request.assert_called_once_with()
    instance._plugin_scheduler.request.assert_called_once_with()
    instance._mcp_scheduler.request.assert_called_once_with(full=True)


@pytest.mark.parametrize("wrapped", [False, True])
async def test_refresh_finds_registry_and_preserves_graph(wrapped):
    instance = runtime()
    registry = SimpleNamespace(refresh=AsyncMock())
    agent = SimpleNamespace(object_agent_registry=registry, checkpointer=object())
    graph = SimpleNamespace(agent=agent) if wrapped else agent
    instance.graph = graph
    await instance._do_agent_refresh()
    registry.refresh.assert_awaited_once()
    assert instance.graph is graph


async def test_plugin_reconcile_requests_object_refresh_after_install(monkeypatch):
    instance = runtime()
    reconcile = AsyncMock(return_value={})
    monkeypatch.setattr("src.progena.plugins.install.reconcile_plugins", reconcile)
    await instance._do_plugin_reconcile()
    reconcile.assert_awaited_once()
    instance._agent_scheduler.request.assert_called_once_with()


async def test_agent_refresh_coalesces_bursts_and_requests_during_refresh():
    started, proceed, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def refresh():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await proceed.wait()
        else:
            completed.set()

    scheduler = AgentSyncScheduler(reconcile=refresh)
    try:
        for _ in range(10):
            scheduler.request()
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(10):
            scheduler.request()
        proceed.set()
        await asyncio.wait_for(completed.wait(), 2)
        assert calls == 2
    finally:
        await scheduler.aclose()


async def test_listener_notification_refreshes_registry(monkeypatch):
    instance = runtime()
    refreshed = asyncio.Event()
    instance.graph = SimpleNamespace(
        object_agent_registry=SimpleNamespace(refresh=AsyncMock(side_effect=refreshed.set))
    )
    scheduler = AgentSyncScheduler(reconcile=instance._do_agent_refresh)
    instance._agent_scheduler = scheduler
    try:
        instance._on_store_change(json.dumps({"key": "agent:user/new/SYSTEM.md"}))
        await asyncio.wait_for(refreshed.wait(), 2)
        instance.graph.object_agent_registry.refresh.assert_awaited_once()
    finally:
        await scheduler.aclose()
