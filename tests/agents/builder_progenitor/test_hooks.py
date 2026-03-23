from unittest.mock import AsyncMock

import pytest

from app.agents.builder_progenitor.hooks import (
    HookContext,
    HookManager,
    create_default_hook_manager,
)


@pytest.mark.asyncio
async def test_hook_manager_emits_specific_handler():
    manager = HookManager()
    handler = AsyncMock()
    manager.register("build_started", handler)

    context = HookContext(event="build_started", task_id="task-1", request_text="build it")
    await manager.emit("build_started", context)

    handler.assert_awaited_once_with(context)


@pytest.mark.asyncio
async def test_default_hook_manager_registers_builder_lifecycle_events():
    handler = AsyncMock()
    manager = create_default_hook_manager(handler)

    context = HookContext(event="build_failed", task_id="task-2", request_text="oops")
    await manager.emit("build_failed", context)

    handler.assert_awaited_once_with(context)
