"""Tests for subprocess lifecycle handling."""

import asyncio
import json

import pytest

from app.progenitor.runtime import agent_runner as agent_runner_module
from app.progenitor.runtime.agent_runner import AgentRunner


class _FakeStreamReader:
    def __init__(self, chunks: list[bytes] | None = None, *, hang: bool = False) -> None:
        self._chunks = list(chunks or [])
        self._hang = hang

    async def read(self, _size: int = -1) -> bytes:
        if self._hang:
            await asyncio.Future()
        await asyncio.sleep(0)
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeStreamWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_chunks: list[bytes] | None = None,
        stderr_chunks: list[bytes] | None = None,
        hang: bool = False,
    ) -> None:
        self.stdin = _FakeStreamWriter()
        self.stdout = _FakeStreamReader(stdout_chunks, hang=hang)
        self.stderr = _FakeStreamReader(stderr_chunks, hang=hang)
        self.pid = 4242
        self.returncode: int | None = 0 if not hang else None
        self.terminate_called = False
        self.kill_called = False
        self.wait_calls = 0
        self._exit_event = asyncio.Event()
        if not hang:
            self._exit_event.set()

    async def wait(self) -> int:
        self.wait_calls += 1
        await self._exit_event.wait()
        return 0 if self.returncode is None else self.returncode

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        raise AssertionError("invoke_agent should not use process.communicate()")

    def terminate(self) -> None:
        self.terminate_called = True

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9
        self._exit_event.set()


@pytest.mark.asyncio
async def test_invoke_agent_drains_large_stdout_without_communicate(tmp_path, monkeypatch):
    """invoke_agent should stream large stdout payloads without relying on communicate."""
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text("print('unused')\n", encoding="utf-8")
    runner = AgentRunner(project_root=tmp_path)

    payload = {"ok": True, "payload": "x" * 200_000}
    encoded = json.dumps(payload).encode()
    chunks = [encoded[i : i + 8192] for i in range(0, len(encoded), 8192)]
    process = _FakeProcess(stdout_chunks=chunks)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        agent_runner_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )

    result = await runner.invoke_agent(
        agent_name="large-agent",
        entrypoint=str(entrypoint),
        request="hello",
        timeout=0.2,
    )

    assert result.success is True
    assert result.response == payload
    assert process.stdin.closed is True
    assert json.loads(process.stdin.buffer.decode())["request"] == "hello"


@pytest.mark.asyncio
async def test_invoke_agent_timeout_kills_and_reaps_process(tmp_path, monkeypatch):
    """Timed-out agent invocation should terminate, kill, and reap the child."""
    entrypoint = tmp_path / "agent.py"
    entrypoint.write_text("print('unused')\n", encoding="utf-8")
    runner = AgentRunner(project_root=tmp_path)
    process = _FakeProcess(hang=True)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        agent_runner_module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(agent_runner_module, "PROCESS_TERMINATE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(agent_runner_module, "PROCESS_KILL_WAIT_TIMEOUT_SECONDS", 0.01)

    result = await runner.invoke_agent(
        agent_name="hung-agent",
        entrypoint=str(entrypoint),
        request="hello",
        timeout=0.01,
    )

    assert result.success is False
    assert result.error == "Timeout after 0.01s"
    assert process.terminate_called is True
    assert process.kill_called is True
    assert process.wait_calls >= 2
