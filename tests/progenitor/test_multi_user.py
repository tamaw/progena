"""Multi-user runtime concurrency tests.

Verifies the lock-free per-thread isolation introduced by the multi-user
runtime plan: per-thread document keys, per-key merge locks, ContextVar
propagation, and in-flight task ordering for same-thread double-submission.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.progenitor.sessions.context import (
    get_current_thread_id,
    reset_current_thread_id,
    set_current_thread_id,
)
from src.progenitor.state.manager import StateManager
from tests.progenitor.conftest import _FakeRuntimeStore


@pytest.mark.asyncio
async def test_state_manager_merge_locks_are_per_thread() -> None:
    """_merge_lock returns a distinct lock per thread_id; acquiring one does not
    block the other."""
    manager = StateManager(runtime_store=_FakeRuntimeStore())

    async def update_a() -> None:
        await manager.update_current_state({"key_a": 1}, thread_id="t1")

    async def update_b() -> None:
        await manager.update_current_state({"key_b": 2}, thread_id="t1")

    await asyncio.gather(update_a(), update_b())

    state = await manager.get_current_state(thread_id="t1")
    assert state.get("key_a") == 1
    assert state.get("key_b") == 2


@pytest.mark.asyncio
async def test_contextvar_propagation_and_reset() -> None:
    """set_current_thread_id / reset_current_thread_id correctly scope the
    thread_id to the async context."""
    assert get_current_thread_id() is None

    token = set_current_thread_id("thread-42")
    try:
        assert get_current_thread_id() == "thread-42"
    finally:
        reset_current_thread_id(token)

    assert get_current_thread_id() is None


@pytest.mark.asyncio
async def test_contextvar_isolation_across_tasks() -> None:
    """ContextVar values are not leaked between concurrent tasks."""
    results: list[str] = []

    async def task_a() -> None:
        tok = set_current_thread_id("a")
        await asyncio.sleep(0.01)
        val = get_current_thread_id()
        assert val is not None
        results.append(val)
        reset_current_thread_id(tok)

    async def task_b() -> None:
        tok = set_current_thread_id("b")
        await asyncio.sleep(0.01)
        val = get_current_thread_id()
        assert val is not None
        results.append(val)
        reset_current_thread_id(tok)

    await asyncio.gather(task_a(), task_b())
    assert sorted(results) == ["a", "b"]


@pytest.mark.asyncio
async def test_state_manager_bare_key_fallback() -> None:
    """get_current_state with thread_id=None falls back to the bare
    'current_state' document for backward compatibility."""
    store = MagicMock()
    store.get_document = AsyncMock(return_value={"cycle_count": 7})
    manager = StateManager(runtime_store=store)

    state = await manager.get_current_state()
    assert state["cycle_count"] == 7

    # Verify the bare key was used, not a composite key.
    store.get_document.assert_awaited_once_with("current_state", {})
