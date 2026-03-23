from unittest.mock import AsyncMock

import pytest

from app.agents.builder_agent.runtime.hooks import (
    HookContext,
    HookManager,
    create_default_hook_manager,
    register_configured_hooks,
)


@pytest.mark.asyncio
async def test_hook_manager_emits_specific_and_wildcard_handlers():
    manager = HookManager()
    specific = AsyncMock()
    wildcard = AsyncMock()

    manager.register("build_started", specific)
    manager.register("*", wildcard)

    context = HookContext(event="build_started", build_id="build-1")
    await manager.emit("build_started", context)

    specific.assert_awaited_once_with(context)
    wildcard.assert_awaited_once_with(context)


@pytest.mark.asyncio
async def test_default_hook_manager_logs_hook_events_to_state_manager():
    state_manager = AsyncMock()
    manager = create_default_hook_manager(state_manager)

    await manager.emit(
        "after_plan",
        HookContext(event="after_plan", build_id="build-2", node_name="plan"),
    )

    state_manager.log_decision.assert_awaited_once()
    payload = state_manager.log_decision.await_args.args[0]
    assert payload["event"] == "hook"
    assert payload["hook_event"] == "after_plan"
    assert payload["node_name"] == "plan"


@pytest.mark.asyncio
async def test_register_configured_hooks_supports_event_specific_handlers():
    state_manager = AsyncMock()
    manager = HookManager()
    register_configured_hooks(
        manager,
        ["build_failed=decision_log", "node_error=python_logger"],
        state_manager=state_manager,
    )

    await manager.emit(
        "build_failed",
        HookContext(event="build_failed", build_id="build-3"),
    )

    state_manager.log_decision.assert_awaited_once()
    payload = state_manager.log_decision.await_args.args[0]
    assert payload["hook_event"] == "build_failed"


def test_runtime_config_reads_hook_registrations_from_env(monkeypatch):
    monkeypatch.setenv(
        "PROGENITOR_BUILD_HOOKS",
        "build_failed=decision_log,node_error=python_logger",
    )

    from app.agents.builder_agent.config import RuntimeConfig

    config = RuntimeConfig.from_env()
    assert config.hook_registrations == [
        "build_failed=decision_log",
        "node_error=python_logger",
    ]
