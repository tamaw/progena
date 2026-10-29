"""Consumer-visible trace subscription races and cache freshness."""

import asyncio
import json

import pytest

from src.backend.runs import events
from src.backend.runs.models import RunTrace


def _trace(run_id, *, status="running", tokens=None):
    return RunTrace(
        kind="workflow",
        run_id=run_id,
        name=run_id,
        status=status,
        started_at="2026-10-28T12:00:00Z",
        tokens=tokens,
    )


@pytest.mark.asyncio
async def test_disconnect_keeps_other_viewers_checkpoint_interest(monkeypatch):
    manager = events.RunsEvents()

    async def build(_store, _kind, run_id):
        return _trace(run_id), {"shared-thread"}

    monkeypatch.setattr(events, "build_trace", build)
    try:
        await manager.subscribe(object(), "first", "workflow", "shared")
        await manager.subscribe(object(), "second", "workflow", "shared")
        manager.disconnected("first")
        assert manager.by_thread["shared-thread"] == {("workflow", "shared")}
        assert manager.interests["second"].run_id == "shared"
        manager.disconnected("second")
        assert "shared-thread" not in manager.by_thread
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_slow_previous_selection_cannot_replace_new_selection(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def build(_store, _kind, run_id):
        if run_id == "old":
            entered.set()
            await release.wait()
        return _trace(run_id), {f"thread-{run_id}"}

    monkeypatch.setattr(events, "build_trace", build)
    old = asyncio.create_task(manager.subscribe(object(), "viewer", "workflow", "old"))
    try:
        await entered.wait()
        newest = await manager.subscribe(object(), "viewer", "workflow", "new")
        release.set()
        assert await old is None
        assert newest is not None and newest[0].run_id == "new"
        assert manager.interests["viewer"].run_id == "new"
        assert "thread-old" not in manager.by_thread
        assert manager.by_thread["thread-new"] == {("workflow", "new")}
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_checkpoint_during_initial_hydration_rebuilds_before_subscription_returns(
    monkeypatch,
):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    builds = 0

    async def build(_store, _kind, run_id):
        nonlocal builds
        builds += 1
        if builds == 1:
            entered.set()
            await release.wait()
        return _trace(run_id, tokens=7 if builds == 1 else 17), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    subscribing = asyncio.create_task(manager.subscribe(object(), "viewer", "workflow", "run"))
    try:
        await entered.wait()
        manager.checkpoint_changed(json.dumps({"thread_id": "thread"}))
        release.set()

        selected = await subscribing

        assert selected is not None
        assert selected[0].tokens == 17
        assert builds == 2
        assert manager.by_thread["thread"] == {("workflow", "run")}
        assert not manager.pending
    finally:
        release.set()
        await asyncio.gather(subscribing, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_change_during_build_discards_stale_result_and_caches_current(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()
    usage = 12

    async def build(_store, _kind, run_id):
        captured_usage = usage
        if captured_usage == 12:
            entered.set()
            await release.wait()
        return _trace(run_id, status="completed", tokens=captured_usage), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    pending = asyncio.create_task(manager.read_trace(object(), "workflow", "run"))
    try:
        await entered.wait()
        usage = 37
        manager._invalidate(("workflow", "run"))
        release.set()
        trace, _ = await pending
        assert trace.tokens == 37
        cached, _ = await manager.read_trace(object(), "workflow", "run")
        assert cached.tokens == 37
        assert manager.completed[("workflow", "run")][0].tokens == 37
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await manager.stop()


@pytest.mark.asyncio
async def test_cancelled_reader_does_not_retain_completed_background_build(monkeypatch):
    manager = events.RunsEvents()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def build(_store, _kind, run_id):
        entered.set()
        await release.wait()
        return _trace(run_id), {"thread"}

    monkeypatch.setattr(events, "build_trace", build)
    reader = asyncio.create_task(manager.read_trace(object(), "workflow", "run"))
    try:
        await entered.wait()
        shared = manager.builds[("workflow", "run")]
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        release.set()
        await shared
        await asyncio.sleep(0)
        assert not manager.builds
        assert not manager.trace_threads
        assert not manager.by_thread
    finally:
        release.set()
        await asyncio.gather(reader, return_exceptions=True)
        await manager.stop()
