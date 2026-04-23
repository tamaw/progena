"""Tests for circuit breaker state transitions."""

import asyncio

import pytest

from app.progenitor.llm.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


def test_record_failure_without_running_loop_uses_async_path(monkeypatch):
    """Sync failure recording should still go through the locked async helper."""
    breaker = CircuitBreaker()
    called = False

    async def fake_record_failure() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(breaker, "_record_failure", fake_record_failure)
    monkeypatch.setattr(
        "app.progenitor.llm.circuit_breaker.asyncio.get_running_loop",
        lambda: (_ for _ in ()).throw(RuntimeError("no running loop")),
    )

    breaker.record_failure()

    assert called is True


def test_record_success_without_running_loop_uses_async_path(monkeypatch):
    """Sync success recording should still go through the locked async helper."""
    breaker = CircuitBreaker()
    called = False

    async def fake_record_success() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(breaker, "_record_success", fake_record_success)
    monkeypatch.setattr(
        "app.progenitor.llm.circuit_breaker.asyncio.get_running_loop",
        lambda: (_ for _ in ()).throw(RuntimeError("no running loop")),
    )

    breaker.record_success()

    assert called is True


@pytest.mark.asyncio
async def test_circuit_moves_from_closed_to_open_at_threshold(monkeypatch):
    """Failures below the threshold stay closed; the threshold edge opens the circuit."""
    now = 100.0
    monkeypatch.setattr("app.progenitor.llm.circuit_breaker.time.time", lambda: now)
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30.0)

    async def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await breaker.call(fail)
    assert breaker.state == "closed"
    assert breaker.failure_count == 1

    with pytest.raises(RuntimeError, match="boom"):
        await breaker.call(fail)
    assert breaker.state == "open"
    assert breaker.failure_count == 2

    with pytest.raises(CircuitOpenError, match="Circuit breaker is open"):
        await breaker.call(fail)


@pytest.mark.asyncio
async def test_circuit_transitions_open_half_open_closed_on_success(monkeypatch):
    """Once the reset timeout expires, a successful probe should close the circuit again."""
    now = 100.0
    monkeypatch.setattr("app.progenitor.llm.circuit_breaker.time.time", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10.0)

    async def fail() -> None:
        raise RuntimeError("boom")

    async def succeed() -> str:
        return "ok"

    with pytest.raises(RuntimeError, match="boom"):
        await breaker.call(fail)
    assert breaker.state == "open"

    now = 111.0
    assert await breaker.call(succeed) == "ok"
    assert breaker.state == "closed"
    assert breaker.failure_count == 0
    assert breaker.get_stats()["half_open_calls"] == 0


@pytest.mark.asyncio
async def test_half_open_failure_reopens_the_circuit(monkeypatch):
    """A failing probe in half-open should immediately reopen the breaker."""
    now = 100.0
    monkeypatch.setattr("app.progenitor.llm.circuit_breaker.time.time", lambda: now)
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5.0)

    async def fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await breaker.call(fail)
    assert breaker.state == "open"

    now = 106.0
    with pytest.raises(RuntimeError, match="boom"):
        await breaker.call(fail)

    assert breaker.state == "open"
    assert breaker.failure_count == 2
    assert breaker.get_stats()["half_open_calls"] == 0


@pytest.mark.asyncio
async def test_half_open_call_cap_is_enforced():
    """Half-open mode should reject calls once the probe budget is exhausted."""
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, half_open_max_calls=2)
    breaker._state = CircuitState.HALF_OPEN
    breaker._half_open_calls = 2

    async def succeed() -> str:
        return "ok"

    with pytest.raises(CircuitOpenError, match="half-open limit reached"):
        await breaker.call(succeed)


@pytest.mark.asyncio
async def test_concurrent_success_and_failure_updates_share_the_same_locking_path():
    """Concurrent state updates should serialize cleanly under contention."""
    breaker = CircuitBreaker(failure_threshold=1000)
    breaker._failures = 100

    await asyncio.gather(
        *(breaker._record_failure() for _ in range(50)),
        *(breaker._record_success() for _ in range(50)),
    )

    assert breaker.failure_count == 100
    assert breaker.state == "closed"
