"""Consumer-visible safety and lifecycle tests for Daytona Python execution."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.progena.sandboxes.daytona as daytona_module
from src.progena.sandboxes.daytona import (
    DaytonaCleanupError,
    DaytonaConfigurationError,
    DaytonaExecutionError,
    DaytonaPythonExecutor,
    DaytonaSettings,
    DaytonaTimeoutError,
    DaytonaTransferError,
    _runner_source,
)


class _Params:
    def __init__(self, **values: Any) -> None:
        self.values = values


class _FakeFileSystem:
    def __init__(self, files: dict[str, bytes], chunk_size: int = 3) -> None:
        self.files = files
        self.chunk_size = chunk_size
        self.uploaded: list[tuple[str, bytes, int]] = []

    async def upload_file(self, content: bytes, path: str, *, timeout: int) -> None:
        self.uploaded.append((path, content, timeout))
        self.files[path] = content

    async def download_file_stream(self, path: str, *, timeout: int):
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]

        async def chunks():
            for index in range(0, len(content), self.chunk_size):
                yield content[index : index + self.chunk_size]

        return chunks()


class _FakeProcess:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self.sandbox = sandbox
        self.commands: list[tuple[str, str | None, int]] = []

    async def exec(self, command: str, *, cwd: str | None = None, timeout: int):
        self.commands.append((command, cwd, timeout))
        if command.startswith("python3 -c "):
            return SimpleNamespace(exit_code=0, result="/tmp/progena-fixture")
        self.sandbox.plan.started.set()
        await asyncio.to_thread(self.sandbox.plan.release.wait)
        assert cwd is not None
        files = self.sandbox.fs.files
        files[f"{cwd}/__progena_stdout.log"] = self.sandbox.plan.stdout
        files[f"{cwd}/__progena_stderr.log"] = self.sandbox.plan.stderr
        if self.sandbox.plan.exit_code == 0:
            files[f"{cwd}/__progena_result.json"] = self.sandbox.plan.result
        for path, content in self.sandbox.plan.outputs.items():
            files[f"{cwd}/{path}"] = content
        return SimpleNamespace(exit_code=self.sandbox.plan.exit_code)


class _FakeSandbox:
    def __init__(self, plan: _Plan) -> None:
        self.id = "sandbox-123"
        self.plan = plan
        self.network_block_all = plan.network_block_all
        self.cpu = plan.cpu
        self.memory = plan.memory
        self.disk = plan.disk
        self.gpu = plan.gpu
        self.fs = _FakeFileSystem(plan.files, plan.chunk_size)
        self.process = _FakeProcess(self)
        self.refreshed = False
        self.ttls: list[tuple[int, int]] = []

    async def refresh_data(self, *, request_timeout: int) -> None:
        self.refreshed = True

    async def set_ttl(self, ttl_minutes: int, *, request_timeout: int) -> None:
        self.ttls.append((ttl_minutes, request_timeout))


class _FakeClient:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan
        self.config: _Params | None = None
        self.create_params: _Params | None = None
        self.create_timeout: int | None = None
        self.deleted: list[tuple[_FakeSandbox, int, bool]] = []
        self.closed = False
        self.sandbox: _FakeSandbox | None = None

    async def create(self, params: _Params, *, timeout: int) -> _FakeSandbox:
        self.create_params = params
        self.create_timeout = timeout
        self.sandbox = _FakeSandbox(self.plan)
        return self.sandbox

    async def delete(self, sandbox: _FakeSandbox, *, timeout: int, wait: bool) -> None:
        self.deleted.append((sandbox, timeout, wait))
        if self.plan.delete_error is not None:
            raise self.plan.delete_error

    async def close(self) -> None:
        self.closed = True


@dataclass
class _Plan:
    result: bytes = b'{"answer":3}'
    stdout: bytes = b"completed\n"
    stderr: bytes = b""
    outputs: dict[str, bytes] = field(default_factory=dict)
    exit_code: int = 0
    network_block_all: bool = True
    delete_error: Exception | None = None
    chunk_size: int = 3
    cpu: int = 1
    memory: int = 1
    disk: int = 3
    gpu: int = 0
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    files: dict[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.release.set()


class _TestExecutor(DaytonaPythonExecutor):
    def __init__(self, settings: DaytonaSettings, client: _FakeClient) -> None:
        super().__init__(settings)
        self.client = client

    def _sdk(self) -> Any:
        return SimpleNamespace(
            DaytonaConfig=_Params,
            CreateSandboxFromSnapshotParams=_Params,
            AsyncDaytona=self._new_client,
        )

    def _new_client(self, config: _Params) -> _FakeClient:
        self.client.config = config
        return self.client


def _executor(plan: _Plan | None = None) -> tuple[_TestExecutor, _FakeClient]:
    client = _FakeClient(plan or _Plan())
    return (
        _TestExecutor(DaytonaSettings(api_key="host-only-key", timeout_seconds=45), client),
        client,
    )


def test_generated_wrapper_executes_user_code_in_its_dedicated_workdir(tmp_path: Path) -> None:
    (tmp_path / "__progena_runner.py").write_bytes(_runner_source())
    (tmp_path / "main.py").write_text(
        'print(f\'answer={inputs["left"] + inputs["right"]}\')\n'
        'result = {"answer": inputs["left"] + inputs["right"]}\n',
        encoding="utf-8",
    )
    (tmp_path / "inputs.json").write_text('{"left":1,"right":2}', encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "__progena_runner.py"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert json.loads((tmp_path / "__progena_result.json").read_text(encoding="utf-8")) == {
        "answer": 3
    }
    assert (tmp_path / "__progena_stdout.log").read_text(encoding="utf-8") == "answer=3\n"


async def test_executes_in_disposable_network_blocked_sandbox_and_collects_selected_files() -> None:
    executor, client = _executor(_Plan(outputs={"reports/summary.csv": b"a,b\n1,2\n"}))

    completed = await executor.execute(
        code="result = {'answer': inputs['left'] + inputs['right']}",
        inputs={"left": 1, "right": 2},
        input_files={"source/data.csv": b"left,right\n1,2\n"},
        output_paths=["reports/summary.csv"],
    )

    assert completed.sandbox_id == "sandbox-123"
    assert completed.result == {"answer": 3}
    assert completed.stdout == "completed\n"
    assert completed.output_files == {"reports/summary.csv": b"a,b\n1,2\n"}
    assert client.sandbox is not None
    assert client.create_timeout is not None and client.create_timeout > 0
    assert client.sandbox.ttls and all(
        ttl_minutes > 0 and request_timeout > 0
        for ttl_minutes, request_timeout in client.sandbox.ttls
    )
    assert client.sandbox.fs.uploaded
    assert all(upload[2] > 0 for upload in client.sandbox.fs.uploaded)
    assert client.sandbox.process.commands[-1][1] is not None
    assert client.sandbox.refreshed is True
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_rejects_oversized_snapshot_profile_before_uploading_or_executing() -> None:
    executor, client = _executor(_Plan(cpu=3))

    with pytest.raises(DaytonaConfigurationError, match="resources exceed"):
        await executor.execute(code="result = None", inputs={})

    assert client.sandbox is not None
    assert client.sandbox.fs.uploaded == []
    assert client.sandbox.process.commands == []
    assert client.deleted == [(client.sandbox, 45, True)]


@pytest.mark.parametrize(
    "resources",
    [
        {"cpu": 0},
        {"memory": float("nan")},
    ],
)
async def test_rejects_invalid_snapshot_profile_before_uploading(
    resources: dict[str, float | int],
) -> None:
    executor, client = _executor(_Plan(**resources))

    with pytest.raises(DaytonaConfigurationError, match="resource limits"):
        await executor.execute(code="result = None", inputs={})

    assert client.sandbox is not None
    assert client.sandbox.fs.uploaded == []
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_enforces_total_deadline_while_streaming_a_download(monkeypatch) -> None:
    async def stream():
        await asyncio.sleep(0.001)
        yield b"first chunk"

    async def download_file_stream(_path, *, timeout):
        return stream()

    sandbox = SimpleNamespace(fs=SimpleNamespace(download_file_stream=download_file_stream))
    monkeypatch.setattr(daytona_module, "_DOWNLOAD_TOTAL_TIMEOUT_SECONDS", 0)
    with pytest.raises(DaytonaTransferError) as caught:
        await DaytonaPythonExecutor._download_limited(sandbox, "/tmp/result", maximum=1024)
    assert isinstance(caught.value.__cause__, TimeoutError)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"input_files": {"../outside.txt": b"x"}}, "normalized relative path"),
        ({"input_files": {"nested//input.txt": b"x"}}, "normalized relative path"),
        ({"input_files": {"nested\\input.txt": b"x"}}, "normalized relative path"),
        ({"output_paths": ["__progena_result.json"]}, "reserved sandbox control file"),
        ({"inputs": {"not_finite": float("nan")}}, "NaN or infinity"),
    ],
)
async def test_rejects_unaddressable_or_nonfinite_boundaries_before_creating_a_sandbox(
    kwargs: dict[str, Any], message: str
) -> None:
    executor, client = _executor()

    with pytest.raises(DaytonaConfigurationError, match=message):
        await executor.execute(**{"code": "result = None", "inputs": {}, **kwargs})

    assert client.create_params is None
    assert client.deleted == []


async def test_reports_nonzero_remote_execution_and_still_deletes_sandbox() -> None:
    executor, client = _executor(_Plan(exit_code=17, stderr=b"ValueError: bad input\n"))

    with pytest.raises(DaytonaExecutionError, match="ValueError: bad input") as raised:
        await executor.execute(code="raise ValueError('bad input')", inputs={})

    assert raised.value.exit_code == 17
    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_rejects_nonfinite_remote_result_and_still_deletes_sandbox() -> None:
    executor, client = _executor(_Plan(result=b'{"not_finite":NaN}'))

    with pytest.raises(DaytonaExecutionError, match="not finite JSON"):
        await executor.execute(code="result = float('nan')", inputs={})

    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_treats_provider_timeout_as_failure_and_deletes_sandbox() -> None:
    executor, client = _executor(_Plan(exit_code=124))

    with pytest.raises(DaytonaTimeoutError, match="timed out after 45 seconds"):
        await executor.execute(code="while True: pass", inputs={})

    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_refuses_tier_that_cannot_confirm_network_block_all_and_deletes_sandbox() -> None:
    executor, client = _executor(_Plan(network_block_all=False))

    with pytest.raises(DaytonaConfigurationError, match="did not report network_block_all"):
        await executor.execute(code="result = None", inputs={})

    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_rejects_oversized_output_stream_without_leaking_sandbox() -> None:
    oversized = b"x" * (16 * 1_048_576 + 1)
    executor, client = _executor(_Plan(outputs={"reports/large.bin": oversized}, chunk_size=65_537))

    with pytest.raises(DaytonaTransferError, match="exceeds"):
        await executor.execute(code="result = None", inputs={}, output_paths=["reports/large.bin"])

    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_cancellation_waits_for_remote_cleanup_before_propagating() -> None:
    plan = _Plan()
    plan.release.clear()
    executor, client = _executor(plan)
    task = asyncio.create_task(executor.execute(code="result = None", inputs={}))

    await asyncio.wait_for(asyncio.to_thread(plan.started.wait), timeout=1)
    task.cancel()
    plan.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]


async def test_preserves_remote_failure_as_context_when_deletion_also_fails() -> None:
    executor, client = _executor(_Plan(exit_code=2, delete_error=RuntimeError("delete denied")))

    with pytest.raises(DaytonaCleanupError, match="delete denied") as raised:
        await executor.execute(code="raise RuntimeError()", inputs={})

    assert isinstance(raised.value.__cause__, DaytonaExecutionError)
    assert client.sandbox is not None
    assert client.deleted == [(client.sandbox, 45, True)]
