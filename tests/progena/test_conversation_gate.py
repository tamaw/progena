"""Regression coverage for runtime-shared checkpoint turn serialization."""

from __future__ import annotations

import asyncio

import pytest

from src.progena.runtime.conversation_gate import ConversationTurnGate

pytestmark = pytest.mark.asyncio


async def test_same_thread_fifo_survives_cancelled_middle_waiter_and_late_arrival() -> None:
    gate = ConversationTurnGate()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    first_active = False
    started: list[str] = []
    queued: list[str] = []

    async def first() -> None:
        nonlocal first_active
        first_active = True
        started.append("first")
        first_started.set()
        try:
            await release_first.wait()
        finally:
            first_active = False

    async def later(name: str) -> None:
        assert not first_active
        started.append(name)

    async def queued_notice(name: str) -> None:
        queued.append(name)

    first_task = asyncio.create_task(gate.run("thread", first))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second_task = asyncio.create_task(
        gate.run("thread", lambda: later("second"), on_queued=lambda: queued_notice("second"))
    )
    third_task = asyncio.create_task(
        gate.run("thread", lambda: later("third"), on_queued=lambda: queued_notice("third"))
    )
    await asyncio.sleep(0)
    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task

    # The first task has been released, but the already-queued third turn has
    # not had an opportunity to run before this newer arrival registers.
    release_first.set()
    fourth_task = asyncio.create_task(
        gate.run("thread", lambda: later("fourth"), on_queued=lambda: queued_notice("fourth"))
    )
    await asyncio.gather(first_task, third_task, fourth_task)

    assert started == ["first", "third", "fourth"]
    assert queued == ["second", "third", "fourth"]
    assert not gate.is_active("thread")


async def test_different_threads_run_in_parallel() -> None:
    gate = ConversationTurnGate()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def turn(started: asyncio.Event) -> None:
        started.set()
        await release.wait()

    first = asyncio.create_task(gate.run("thread-a", lambda: turn(first_started)))
    second = asyncio.create_task(gate.run("thread-b", lambda: turn(second_started)))
    await asyncio.wait_for(asyncio.gather(first_started.wait(), second_started.wait()), timeout=1)
    release.set()
    await asyncio.gather(first, second)


async def test_same_task_reentry_does_not_wait_for_its_own_turn() -> None:
    gate = ConversationTurnGate()

    async def nested() -> str:
        return "nested"

    async def outer() -> str:
        return await gate.run("thread", nested)

    assert await asyncio.wait_for(gate.run("thread", outer), timeout=1) == "nested"
