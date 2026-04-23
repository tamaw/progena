"""Tests for HTTP agent lifecycle management."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.runtime.http_agent_manager import HTTPAgentManager, ManagedAgent
from app.progenitor.state.models import AgentStatus


class FakeProcess:
    """Minimal subprocess double for shutdown tests."""

    def __init__(self) -> None:
        self.returncode = None
        self.events: list[str] = []
        self.wait_calls = 0

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")

    async def wait(self) -> None:
        self.wait_calls += 1
        self.events.append(f"wait_{self.wait_calls}")
        if self.wait_calls == 1:
            raise TimeoutError


@pytest.mark.asyncio
async def test_stop_agent_reaps_process_after_forced_kill(tmp_path) -> None:
    registry = MagicMock()
    registry.mark_agent_stopped = AsyncMock(return_value=True)

    manager = HTTPAgentManager(tmp_path, registry)
    process = FakeProcess()
    manager._agents["hung-agent"] = ManagedAgent(
        name="hung-agent",
        entrypoint="agent.py",
        port=8123,
        pid=12345,
        process=process,
        started_at=datetime.now(UTC),
    )

    stopped = await manager.stop_agent("hung-agent")

    assert stopped is True
    assert process.events == ["terminate", "wait_1", "kill", "wait_2"]
    assert process.wait_calls == 2
    registry.mark_agent_stopped.assert_awaited_once_with("hung-agent")
    assert manager.get_agent("hung-agent") is None


@pytest.mark.asyncio
async def test_status_update_parses_runtime_port_and_updates_registry(tmp_path) -> None:
    registry = MagicMock()
    registry.update_agent_status = AsyncMock(return_value=True)

    manager = HTTPAgentManager(tmp_path, registry)
    agent = ManagedAgent(
        name="dynamic-agent",
        entrypoint="agent.py",
        port=0,
        pid=12345,
        process=MagicMock(),
        started_at=datetime.now(UTC),
    )

    await manager._handle_status_update(agent, "running", "Server started on port 43123")

    assert agent.port == 43123
    assert agent.status == AgentStatus.RUNNING
    registry.update_agent_status.assert_awaited_once_with(
        "dynamic-agent",
        AgentStatus.RUNNING,
        status_message="Server started on port 43123",
        port=43123,
        update_heartbeat=True,
    )


class FakeResponseContext:
    """Minimal async response context manager."""

    def __init__(self, status: int) -> None:
        self._response = MagicMock(status=status)

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeSession:
    """Minimal aiohttp session double."""

    def __init__(self) -> None:
        self.closed = False
        self.get_calls: list[tuple[str, object]] = []

    def get(self, url: str, *, timeout):
        self.get_calls.append((url, timeout))
        return FakeResponseContext(200)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_health_checks_reuse_manager_owned_http_session(tmp_path, monkeypatch) -> None:
    registry = MagicMock()
    manager = HTTPAgentManager(tmp_path, registry)
    created_sessions: list[FakeSession] = []

    def session_factory() -> FakeSession:
        session = FakeSession()
        created_sessions.append(session)
        return session

    monkeypatch.setattr(
        "app.progenitor.runtime.http_agent_manager.aiohttp.ClientSession",
        session_factory,
    )

    agent = ManagedAgent(
        name="healthy-agent",
        entrypoint="agent.py",
        port=8123,
        pid=12345,
        process=MagicMock(returncode=None),
        started_at=datetime.now(UTC),
    )

    assert await manager._health_check_agent(agent) is True
    assert await manager._health_check_agent(agent) is True

    assert len(created_sessions) == 1
    assert len(created_sessions[0].get_calls) == 2


@pytest.mark.asyncio
async def test_stop_all_closes_reusable_http_session(tmp_path, monkeypatch) -> None:
    registry = MagicMock()
    manager = HTTPAgentManager(tmp_path, registry)
    session = FakeSession()

    monkeypatch.setattr(
        "app.progenitor.runtime.http_agent_manager.aiohttp.ClientSession",
        lambda: session,
    )

    created = await manager._get_http_session()
    assert created is session

    await manager.stop_all()

    assert session.closed is True
