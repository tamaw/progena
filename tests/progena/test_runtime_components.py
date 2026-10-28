"""Tests for Progena runtime components."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import src.progena.orchestration.nodes.health as health_module
from src.progena.config import ProgenaConfig
from src.progena.mcp.runtime import MCPRuntime
from src.progena.orchestration.nodes.health import write_health_status
from src.progena.orchestration.nodes.mcp import sync_mcp_runtime_context
from src.progena.runtime.queues import QueuedInput, RuntimeQueues


@pytest.mark.asyncio
async def test_health_node_persists_status() -> None:
    state_manager = MagicMock()
    state_manager.save_health = AsyncMock()
    await write_health_status(state_manager, "offline")

    state_manager.save_health.assert_awaited_once()
    saved = state_manager.save_health.await_args.args[0]
    assert saved["status"] == "offline"
    assert "started_at" in saved
    # version is None outside a git checkout with no GIT_COMMIT env var set
    # (e.g. some CI sandboxes); just confirm the key is present either way.
    assert "version" in saved


def test_detect_git_commit_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMIT", "abc123def456extra")
    assert health_module._detect_git_commit() == "abc123def456"


def test_detect_git_commit_falls_back_to_github_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "deadbeef0000")
    assert health_module._detect_git_commit() == "deadbeef0000"


@pytest.mark.asyncio
async def test_runtime_queues_track_wait_and_pending_counts() -> None:
    config = ProgenaConfig()
    queues = RuntimeQueues(config.runtime)

    await queues.enqueue_input("one")
    await queues.enqueue_input("two")
    queued = queues.input_queue.get_nowait()

    assert isinstance(queued, QueuedInput)
    await queues.input_queue.put(queued)
    assert await queues.get_input(timeout=0.01) == "two"
    assert queues.current_input_pending_count == 1


@pytest.mark.asyncio
async def test_apply_mcp_runtime_refresh_updates_runtime_and_registry(monkeypatch) -> None:
    import src.progena.mcp.context as mcp_tools

    config = ProgenaConfig().mcp
    runtime = MCPRuntime(config)
    runtime.close = AsyncMock()  # type: ignore[method-assign]

    llm_client = MagicMock()
    llm_client.mcp_runtime = runtime
    llm_client.mcp_config = config
    tool_registry = MagicMock()

    async def fake_build_mcp_runtime_context(_config):
        return (
            {"remote": {"client": object()}},
            [
                {
                    "name": "search_web",
                    "description": "Search web",
                    "mcp_server_name": "remote",
                    "mcp_server_url": "https://mcp.example.test",
                    "capabilities": ["search_web"],
                }
            ],
            {"remote": {"status": "connected", "error": None, "tool_count": 1}},
        )

    async def fake_build_mcp_langchain_tool_wrappers(_config, _tools):
        return []

    monkeypatch.setattr(mcp_tools, "build_mcp_runtime_context", fake_build_mcp_runtime_context)
    monkeypatch.setattr(
        mcp_tools,
        "build_mcp_langchain_tool_wrappers",
        fake_build_mcp_langchain_tool_wrappers,
    )

    summary = await sync_mcp_runtime_context(
        llm_client=llm_client,
        tool_registry=tool_registry,
        mcp_config=config,
        reload_config=False,
    )

    runtime.close.assert_awaited_once()
    llm_client.set_mcp_runtime.assert_called_once_with(runtime)
    tool_registry.set_runtime_tools.assert_called_once()
    assert runtime.mcp_tools[0]["name"] == "search_web"
    assert summary["server_count"] == 1
    assert summary["tool_count"] == 1
