"""Tests for Progenitor runtime components."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.config import ProgenitorConfig, StorageConfig
from app.progenitor.mcp import MCPRuntime
from app.progenitor.nodes import health as health_module
from app.progenitor.nodes.audit import write_startup_config_audit_to_sink
from app.progenitor.nodes.health import write_health_status
from app.progenitor.nodes.mcp import sync_mcp_runtime_context
from app.progenitor.registry import AgentRegistryRuntime
from app.progenitor.runtime.http import ProgenitorHTTPServer
from app.progenitor.runtime.queues import QueuedInput, RuntimeQueues


def _make_config(tmp_path: Path) -> ProgenitorConfig:
    config = ProgenitorConfig()
    config.storage = StorageConfig(
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "data" / "state",
        log_dir=tmp_path / "logs",
        worktree_dir=tmp_path / "worktrees",
        prompts_dir=tmp_path / "data" / "prompts",
    )
    config.llm.api_key = ""
    config.llm.base_url = ""
    return config


def test_runtime_components_wire_configured_paths(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    state_dir = config.storage.state_dir
    config_dir = state_dir.parent / "config"
    registry = AgentRegistryRuntime(config_dir, tmp_path)
    http = ProgenitorHTTPServer(
        state_manager=None,
        host=config.runtime.http_host,
        port=config.runtime.http_port,
        public_base_url=config.runtime.http_public_base_url,
    )

    assert state_dir == tmp_path / "data" / "state"
    assert config_dir == tmp_path / "data" / "config"
    assert registry.agent_registry.registry_path == tmp_path / "data" / "config" / "agents.json"
    assert http.host == config.runtime.http_host
    assert http.port == config.runtime.http_port


@pytest.mark.asyncio
async def test_audit_and_health_nodes_write_operational_files(tmp_path: Path) -> None:
    config = _make_config(tmp_path)

    log_sink = MagicMock()
    log_sink.write_log = AsyncMock()
    await write_startup_config_audit_to_sink(
        log_sink,
        config.to_dict(redact_secrets=True),
    )

    state_manager = MagicMock()
    state_manager.save_health = AsyncMock()
    await write_health_status(state_manager, "offline")

    log_sink.write_log.assert_awaited_once()
    assert log_sink.write_log.await_args.kwargs["payload"]["event"] == "startup_config"
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
    config = ProgenitorConfig()
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
    import app.progenitor.mcp as mcp_tools

    config = ProgenitorConfig().mcp
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
