"""Tests for PluginSyncScheduler single-flight + coalescing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.progena.runtime.plugin_sync import PluginSyncScheduler


@pytest.mark.asyncio
async def test_scheduler_runs_reconcile():
    """A single request schedules and runs the reconcile callable."""
    reconcile = AsyncMock()
    sched = PluginSyncScheduler(reconcile=reconcile)
    sched.request()
    await asyncio.sleep(0.02)
    await sched.aclose()
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduler_coalesces_burst_into_single_extra_run():
    """Two requests while reconcile is in-flight collapse to exactly one extra run."""
    proceed = asyncio.Event()
    started = asyncio.Event()
    calls: list[str] = []

    async def reconcile() -> None:
        calls.append("reconcile")
        started.set()
        await proceed.wait()

    sched = PluginSyncScheduler(reconcile=reconcile)
    sched.request()
    await started.wait()  # first run is in-flight, blocked
    # A burst of requests while one is running must collapse to a single re-run.
    for _ in range(5):
        sched.request()
    proceed.set()
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls.count("reconcile") == 2  # 1 in-flight + 1 coalesced, not 6


@pytest.mark.asyncio
async def test_scheduler_survives_work_exception():
    """An exception inside reconcile is logged and swallowed; the scheduler keeps working."""
    calls: list[str] = []

    async def reconcile() -> None:
        calls.append("reconcile")
        raise RuntimeError("boom")

    sched = PluginSyncScheduler(reconcile=reconcile)
    sched.request()
    await asyncio.sleep(0.02)
    sched.request()  # still works after a prior failure
    await asyncio.sleep(0.02)
    await sched.aclose()
    assert calls.count("reconcile") == 2
