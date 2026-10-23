"""Explicit file exchange between virtual documents and disposable Python sandboxes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from deepagents.backends.protocol import BackendProtocol
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.progena.artifacts.contracts import validate_artifact, validate_artifact_path
from src.progena.sandboxes.daytona import DaytonaPythonExecutor

MAX_INPUT_FILE_BYTES = 1024 * 1024
MAX_INPUT_TOTAL_BYTES = 4 * MAX_INPUT_FILE_BYTES
READABLE_ROOTS = (
    "/artifacts/",
    "/shared/",
    "/attachments/",
    "/instructions/",
    "/routines/",
    "/workflows/",
)


def _virtual_path(path: str) -> str:
    if (
        not path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/")[1:])
    ):
        raise ValueError(f"Expected a canonical absolute virtual file path: {path!r}")
    return path


def _relative_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError(f"Expected a relative sandbox file path: {path!r}")
    return str(PurePosixPath(path))


class SandboxPythonRequest(BaseModel):
    """Run Python with an ``inputs`` object and a required JSON ``result`` value."""

    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None, description="Python source assigning result; no host shell."
    )
    code_file: str | None = Field(
        default=None, description="Explicit virtual path to Python source."
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_files: dict[str, str] = Field(
        default_factory=dict, description="Relative sandbox destination -> virtual source path."
    )
    output_files: dict[str, str] = Field(
        default_factory=dict,
        description="Relative sandbox source -> persistent /artifacts/ or /shared/ destination.",
    )
    timeout_seconds: int | None = Field(default=None, ge=1, le=300, strict=True)

    @model_validator(mode="after")
    def validate_request(self) -> SandboxPythonRequest:
        if (self.code is None) == (self.code_file is None):
            raise ValueError("Specify exactly one of code or code_file")
        if self.code is not None and (
            not self.code.strip() or len(self.code.encode("utf-8")) > 64 * 1024
        ):
            raise ValueError("Python code must be nonempty and at most 64 KiB")
        if self.code_file is not None:
            _validate_source(self.code_file)
        if len(self.input_files) > 16 or len(self.output_files) > 16:
            raise ValueError("At most 16 input files and 16 output files are supported")
        for target, source in self.input_files.items():
            _relative_path(target)
            _validate_source(source)
        for source, target in self.output_files.items():
            _relative_path(source)
            _virtual_path(target)
            if target.startswith("/artifacts/"):
                validate_artifact_path(target.removeprefix("/artifacts/"))
            elif not target.startswith("/shared/"):
                raise ValueError("Output files must target /artifacts/ or configured /shared/")
        if len(set(self.output_files.values())) != len(self.output_files):
            raise ValueError("Output destinations must be distinct")
        payload = json.dumps(self.inputs, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_INPUT_FILE_BYTES:
            raise ValueError("JSON inputs exceed 1 MiB")
        return self


SandboxPythonRunner = Callable[[SandboxPythonRequest], Awaitable[dict[str, Any]]]


def _validate_source(path: str) -> None:
    _virtual_path(path)
    if not path.startswith(READABLE_ROOTS):
        raise ValueError("Sandbox sources must use explicit document, attachment, or shared mounts")


def _declared_mount(backend: BackendProtocol, path: str) -> tuple[Any, str]:
    routes = getattr(backend, "routes", {})
    for prefix in sorted(routes, key=len, reverse=True):
        normalized = prefix.rstrip("/") + "/"
        if path.startswith(normalized):
            return routes[prefix], "/" + path[len(normalized) :]
    raise ValueError(f"No declared virtual mount permits sandbox file {path!r}")


async def _download(backend: BackendProtocol, path: str) -> bytes:
    if path.startswith(("/shared/", "/attachments/")):
        route, relative = _declared_mount(backend, path)
        reader = getattr(route, "adownload_file_bounded", None)
        if not callable(reader):
            raise ValueError(f"Mount does not support bounded raw sandbox imports: {path!r}")
        read_bytes = cast(Callable[[str, int], Awaitable[bytes]], reader)
        return await read_bytes(relative, MAX_INPUT_FILE_BYTES)
    response = (await backend.adownload_files([path]))[0]
    if response.error or response.content is None:
        raise ValueError(f"Cannot read sandbox input {path!r}: {response.error or 'no content'}")
    if len(response.content) > MAX_INPUT_FILE_BYTES:
        raise ValueError(f"Sandbox input {path!r} exceeds 1 MiB")
    return response.content


def create_sandbox_python_runner(
    executor: DaytonaPythonExecutor,
    backend: BackendProtocol,
    *,
    path_policy: Callable[[Literal["read", "write"], str], bool] | None = None,
) -> SandboxPythonRunner:
    """Use existing validated mounts without replacing the conversation backend."""

    async def run(request: SandboxPythonRequest) -> dict[str, Any]:
        reads = list(request.input_files.values())
        if request.code_file:
            reads.append(request.code_file)
        for path in reads:
            _declared_mount(backend, path)
            if path_policy is not None and not path_policy("read", path):
                raise ValueError(f"Sandbox input permission denied: {path!r}")
        for path in request.output_files.values():
            route, relative = _declared_mount(backend, path)
            if path_policy is not None and not path_policy("write", path):
                raise ValueError(f"Sandbox output permission denied: {path!r}")
            checker = getattr(route, "can_write_file", None)
            if callable(checker):
                can_write = cast(Callable[[str, str, dict[str, Any]], Awaitable[bool]], checker)
                if not await can_write(relative, "", {}):
                    raise ValueError(f"Sandbox output mount is read-only: {path!r}")
        code = request.code
        if request.code_file is not None:
            code = (await _download(backend, request.code_file)).decode("utf-8")
        assert code is not None
        if not code.strip() or len(code.encode("utf-8")) > 64 * 1024:
            raise ValueError("Python code must be nonempty and at most 64 KiB")
        files: dict[str, bytes] = {}
        total_bytes = 0
        for target, source in request.input_files.items():
            content = await _download(backend, source)
            total_bytes += len(content)
            if total_bytes > MAX_INPUT_TOTAL_BYTES:
                raise ValueError("Combined sandbox input files exceed 4 MiB")
            files[target] = content
        execution = await executor.execute(
            code=code,
            inputs=request.inputs,
            input_files=files,
            output_paths=list(request.output_files),
            timeout_seconds=request.timeout_seconds,
        )
        # Validate every returned file before writing any destination. Persistence
        # errors can still leave partial exports; cross-mount writes are not atomic.
        artifact_text: dict[str, str] = {}
        for source, target in request.output_files.items():
            content = execution.output_files[source]
            if len(content) > MAX_INPUT_FILE_BYTES:
                raise ValueError(f"Sandbox output {source!r} exceeds 1 MiB")
            if target.startswith("/artifacts/"):
                text = content.decode("utf-8")
                validate_artifact(target.removeprefix("/artifacts/"), text)
                artifact_text[target] = text
        saved: list[str] = []
        for source, target in request.output_files.items():
            if target in artifact_text:
                written = await backend.awrite(target, artifact_text[target])
                error = written.error
            else:
                uploaded = await backend.aupload_files([(target, execution.output_files[source])])
                error = uploaded[0].error
            if error:
                raise ValueError(
                    f"Sandbox {execution.sandbox_id} was deleted, but export to {target!r} "
                    f"failed: {error}. Already saved: {saved}"
                )
            saved.append(target)
        return {
            "sandbox_id": execution.sandbox_id,
            "result": execution.result,
            "stdout": execution.stdout,
            "stderr": execution.stderr,
            "output_files": saved,
            "deleted": True,
        }

    return run
