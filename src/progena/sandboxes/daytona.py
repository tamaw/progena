"""Disposable Daytona-backed Python execution for Progena."""

from __future__ import annotations

import asyncio
import json
import math
import posixpath
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from src.progena.security.redaction import redact_secrets

_MAX_CODE_BYTES = 1_048_576
_MAX_INPUT_BYTES = 8 * 1_048_576
_MAX_RESULT_BYTES = 1_048_576
_MAX_OUTPUT_FILE_BYTES = 16 * 1_048_576
_MAX_OUTPUT_TOTAL_BYTES = 32 * 1_048_576
_MAX_LOG_BYTES = 256 * 1_024
_MAX_TIMEOUT_SECONDS = 300
_SDK_REQUEST_TIMEOUT_SECONDS = 30
_MAX_SANDBOX_CPU = 2
_MAX_SANDBOX_MEMORY_GIB = 4
_MAX_SANDBOX_DISK_GIB = 8
_MAX_SANDBOX_INPUT_FILES = 16
_MAX_SANDBOX_OUTPUT_FILES = 16
_MAX_UPLOAD_COUNT = 3 + _MAX_SANDBOX_INPUT_FILES
_MAX_DOWNLOAD_COUNT = 3 + _MAX_SANDBOX_OUTPUT_FILES
_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 30
_RESERVED_FILES = {
    "main.py",
    "inputs.json",
    "__progena_runner.py",
    "__progena_result.json",
    "__progena_stdout.log",
    "__progena_stderr.log",
}


class DaytonaSandboxError(RuntimeError):
    """Base error for a Daytona-backed Python invocation."""


class DaytonaConfigurationError(DaytonaSandboxError):
    """The local Daytona configuration or provider capability is unusable."""


class DaytonaProvisioningError(DaytonaSandboxError):
    """Daytona could not create the disposable sandbox."""


class DaytonaTransferError(DaytonaSandboxError):
    """A bounded file transfer to or from a sandbox failed."""


class DaytonaExecutionError(DaytonaSandboxError):
    """The remote Python program failed or did not produce a valid result."""

    def __init__(self, sandbox_id: str, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(f"Daytona sandbox {sandbox_id}: {message}")
        self.sandbox_id = sandbox_id
        self.exit_code = exit_code


class DaytonaTimeoutError(DaytonaExecutionError):
    """The remote Python program exceeded its configured execution limit."""


class DaytonaCleanupError(DaytonaSandboxError):
    """The disposable sandbox could not be deleted."""

    def __init__(self, sandbox_id: str, message: str) -> None:
        super().__init__(f"Daytona sandbox {sandbox_id}: cleanup failed: {message}")
        self.sandbox_id = sandbox_id


@dataclass(frozen=True)
class DaytonaSettings:
    """Host-only configuration for one-off Daytona sandboxes."""

    api_key: str = field(repr=False)
    api_url: str | None = None
    target: str | None = None
    snapshot: str | None = None
    timeout_seconds: int = 120
    network_block_all: bool = True


@dataclass(frozen=True)
class PythonExecutionResult:
    """Values collected from a completed, deleted Daytona sandbox."""

    sandbox_id: str
    result: Any
    stdout: str
    stderr: str
    output_files: dict[str, bytes]


def _validate_json(value: Any, *, name: str) -> None:
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DaytonaConfigurationError(f"{name} must not contain NaN or infinity")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item, name=name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DaytonaConfigurationError(f"{name} object keys must be strings")
            _validate_json(item, name=name)
        return
    raise DaytonaConfigurationError(f"{name} must be JSON-serializable")


def _json_bytes(value: dict[str, Any], *, name: str, maximum: int) -> bytes:
    _validate_json(value, name=name)
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DaytonaConfigurationError(f"{name} must be JSON-serializable") from error
    if len(encoded) > maximum:
        raise DaytonaConfigurationError(f"{name} exceeds {maximum} bytes")
    return encoded


def _relative_path(path: str, *, name: str) -> str:
    if not isinstance(path, str) or not path:
        raise DaytonaConfigurationError(f"{name} must be a non-empty relative path")
    raw_parts = path.split("/")
    if (
        "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise DaytonaConfigurationError(f"{name} must be a normalized relative path")
    candidate = PurePosixPath(path)
    if candidate.is_absolute():
        raise DaytonaConfigurationError(f"{name} must be a normalized relative path")
    if candidate.name in _RESERVED_FILES:
        raise DaytonaConfigurationError(f"{name} addresses a reserved sandbox control file")
    return candidate.as_posix()


def _timeout(value: int | None, default: int) -> int:
    effective = default if value is None else value
    if isinstance(effective, bool) or not isinstance(effective, int):
        raise DaytonaConfigurationError("timeout_seconds must be an integer")
    if not 0 < effective <= _MAX_TIMEOUT_SECONDS:
        raise DaytonaConfigurationError(
            f"timeout_seconds must be between 1 and {_MAX_TIMEOUT_SECONDS} seconds"
        )
    return effective


def _decode_log(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _error_preview(content: bytes) -> str:
    text = redact_secrets(_decode_log(content))
    printable = "".join(
        character if character in "\n\t" or character.isprintable() else " " for character in text
    )
    return printable[-4_096:].strip()


def _runner_source() -> bytes:
    """Return the fixed remote wrapper; user code never controls its protocol."""
    return f"""import contextlib
import io
import json
import os
import sys
import traceback

_MAX_LOG_BYTES = {_MAX_LOG_BYTES}
_MAX_RESULT_BYTES = {_MAX_RESULT_BYTES}


class _BoundedText(io.TextIOBase):
    _MARKER = b"\\n[Progena log truncated]\\n"

    def __init__(self, path):
        self._raw = open(path, "wb")
        self._written = 0
        self._truncated = False

    def writable(self):
        return True

    def write(self, value):
        if not isinstance(value, str):
            value = str(value)
        data = value.encode("utf-8", errors="backslashreplace")
        limit = _MAX_LOG_BYTES - len(self._MARKER)
        remaining = limit - self._written
        if remaining > 0:
            self._raw.write(data[:remaining])
            self._written += min(len(data), remaining)
        if len(data) > remaining:
            self._truncated = True
        return len(value)

    def flush(self):
        if not self._raw.closed:
            self._raw.flush()

    def close(self):
        if not self.closed:
            if self._truncated:
                self._raw.write(self._MARKER)
            super().close()
            self._raw.close()


def _write_result(value):
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_RESULT_BYTES:
        raise ValueError("result exceeds Progena's maximum result size")
    temporary = ".__progena_result.tmp"
    with open(temporary, "wb") as handle:
        handle.write(raw)
    os.replace(temporary, "__progena_result.json")


def main():
    stdout = _BoundedText("__progena_stdout.log")
    stderr = _BoundedText("__progena_stderr.log")
    try:
        inputs = json.load(open("inputs.json", encoding="utf-8"))
        namespace = {{"__name__": "__main__", "__file__": os.path.abspath("main.py"), "inputs": inputs}}
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            source = open("main.py", encoding="utf-8").read()
            exec(compile(source, "main.py", "exec"), namespace, namespace)
            if "result" not in namespace:
                raise RuntimeError("Python code must assign the result global")
            _write_result(namespace["result"])
        return 0
    except BaseException:
        traceback.print_exc(file=stderr)
        return 1
    finally:
        stdout.close()
        stderr.close()


sys.exit(main())
""".encode()


_RUNNER_SOURCE = _runner_source()


class DaytonaPythonExecutor:
    """Run supplied Python once in a newly-created, always-deleted Daytona sandbox."""

    def __init__(self, settings: DaytonaSettings) -> None:
        self._settings = settings

    async def execute(
        self,
        *,
        code: str,
        inputs: dict[str, Any],
        input_files: dict[str, bytes] | None = None,
        output_paths: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> PythonExecutionResult:
        """Run Python remotely and collect its finite JSON result and requested files."""
        validated = self._validate_request(
            code=code,
            inputs=inputs,
            input_files=input_files,
            output_paths=output_paths,
            timeout_seconds=timeout_seconds,
        )
        return await self._execute_async(**validated)

    def _validate_request(
        self,
        *,
        code: str,
        inputs: dict[str, Any],
        input_files: dict[str, bytes] | None,
        output_paths: list[str] | None,
        timeout_seconds: int | None,
    ) -> dict[str, Any]:
        if not isinstance(code, str):
            raise DaytonaConfigurationError("code must be a string")
        code_bytes = code.encode("utf-8")
        if len(code_bytes) > _MAX_CODE_BYTES:
            raise DaytonaConfigurationError(f"code exceeds {_MAX_CODE_BYTES} bytes")
        if not isinstance(inputs, dict):
            raise DaytonaConfigurationError("inputs must be an object")
        input_json = _json_bytes(inputs, name="inputs", maximum=_MAX_INPUT_BYTES)
        timeout = _timeout(timeout_seconds, self._settings.timeout_seconds)

        if input_files is not None and not isinstance(input_files, dict):
            raise DaytonaConfigurationError("input_files must be an object")
        if input_files is not None and len(input_files) > _MAX_SANDBOX_INPUT_FILES:
            raise DaytonaConfigurationError(
                f"input_files must contain at most {_MAX_SANDBOX_INPUT_FILES} files"
            )
        files: dict[str, bytes] = {}
        total_input_bytes = len(code_bytes) + len(input_json) + len(_RUNNER_SOURCE)
        for path, content in (input_files or {}).items():
            normalized = _relative_path(path, name="input file path")
            if normalized in files:
                raise DaytonaConfigurationError("input file paths must be unique")
            if not isinstance(content, bytes):
                raise DaytonaConfigurationError("input file content must be bytes")
            total_input_bytes += len(content)
            if total_input_bytes > _MAX_INPUT_BYTES:
                raise DaytonaConfigurationError(
                    f"code and input files exceed {_MAX_INPUT_BYTES} bytes"
                )
            files[normalized] = content

        if output_paths is not None and not isinstance(output_paths, list):
            raise DaytonaConfigurationError("output_paths must be a list")
        if output_paths is not None and len(output_paths) > _MAX_SANDBOX_OUTPUT_FILES:
            raise DaytonaConfigurationError(
                f"output_paths must contain at most {_MAX_SANDBOX_OUTPUT_FILES} files"
            )
        outputs: list[str] = []
        for path in output_paths or []:
            normalized = _relative_path(path, name="output file path")
            if normalized in outputs:
                raise DaytonaConfigurationError("output file paths must be unique")
            outputs.append(normalized)

        return {
            "code": code_bytes,
            "inputs": input_json,
            "input_files": files,
            "output_paths": outputs,
            "timeout_seconds": timeout,
        }

    async def _execute_async(
        self,
        *,
        code: bytes,
        inputs: bytes,
        input_files: dict[str, bytes],
        output_paths: list[str],
        timeout_seconds: int,
    ) -> PythonExecutionResult:
        daytona = self._sdk()
        sandbox: Any | None = None
        client: Any | None = None
        sandbox_id = "<uncreated>"
        primary_error: BaseException | None = None
        result: PythonExecutionResult | None = None
        try:
            try:
                client = daytona.AsyncDaytona(self._sdk_config(daytona))
            except DaytonaConfigurationError:
                raise
            except Exception as error:
                raise DaytonaConfigurationError("Daytona client initialization failed") from error
            if client is None:
                raise DaytonaConfigurationError("Daytona client initialization returned no client")
            try:
                sandbox = await asyncio.wait_for(
                    client.create(self._create_params(daytona), timeout=timeout_seconds),
                    timeout=timeout_seconds,
                )
            except Exception as error:
                raise DaytonaProvisioningError("Daytona sandbox creation failed") from error
            if sandbox is None:
                raise DaytonaProvisioningError("Daytona sandbox creation returned no sandbox")
            sandbox_id = str(sandbox.id)
            self._verify_resource_policy(sandbox)
            await asyncio.wait_for(
                self._verify_network_policy(sandbox),
                timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
            )
            workdir = await asyncio.wait_for(
                self._prepare_workdir(sandbox, input_files),
                timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
            )
            uploads = [
                (posixpath.join(workdir, "main.py"), code),
                (posixpath.join(workdir, "inputs.json"), inputs),
                (posixpath.join(workdir, "__progena_runner.py"), _RUNNER_SOURCE),
                *[
                    (posixpath.join(workdir, path), content)
                    for path, content in input_files.items()
                ],
            ]
            for path, content in uploads:
                try:
                    await asyncio.wait_for(
                        sandbox.fs.upload_file(content, path, timeout=_SDK_REQUEST_TIMEOUT_SECONDS),
                        timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
                    )
                except Exception as error:
                    raise DaytonaTransferError(
                        f"Daytona sandbox {sandbox_id}: upload failed"
                    ) from error
            try:
                await asyncio.wait_for(
                    sandbox.set_ttl(
                        self._execution_ttl_minutes(timeout_seconds),
                        request_timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
                    ),
                    timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
                )
                response = await asyncio.wait_for(
                    sandbox.process.exec(
                        "python3 __progena_runner.py >/dev/null 2>&1",
                        cwd=workdir,
                        timeout=timeout_seconds,
                    ),
                    timeout=timeout_seconds + 5,
                )
            except TimeoutError as error:
                raise DaytonaTimeoutError(
                    sandbox_id,
                    f"execution timed out after {timeout_seconds} seconds",
                ) from error
            except Exception as error:
                raise DaytonaExecutionError(
                    sandbox_id, "Daytona command execution failed"
                ) from error
            exit_code = getattr(response, "exit_code", None)
            if exit_code == 124:
                raise DaytonaTimeoutError(
                    sandbox_id,
                    f"execution timed out after {timeout_seconds} seconds",
                    exit_code=exit_code,
                )
            stdout = await self._download_limited(
                sandbox, posixpath.join(workdir, "__progena_stdout.log"), _MAX_LOG_BYTES
            )
            stderr = await self._download_limited(
                sandbox, posixpath.join(workdir, "__progena_stderr.log"), _MAX_LOG_BYTES
            )
            if exit_code != 0:
                detail = _error_preview(stderr)
                output = _error_preview(stdout)
                message = "Python execution failed"
                if detail:
                    message += f": {detail}"
                if output:
                    message += f" (stdout: {output})"
                raise DaytonaExecutionError(sandbox_id, message, exit_code=exit_code)
            control = await self._download_limited(
                sandbox, posixpath.join(workdir, "__progena_result.json"), _MAX_RESULT_BYTES
            )
            parsed = self._parse_result(control, sandbox_id)
            downloaded: dict[str, bytes] = {}
            total_output = 0
            for path in output_paths:
                content = await self._download_limited(
                    sandbox, posixpath.join(workdir, path), _MAX_OUTPUT_FILE_BYTES
                )
                total_output += len(content)
                if total_output > _MAX_OUTPUT_TOTAL_BYTES:
                    raise DaytonaTransferError(
                        f"Daytona sandbox {sandbox_id}: requested output files exceed "
                        f"{_MAX_OUTPUT_TOTAL_BYTES} bytes"
                    )
                downloaded[path] = content
            result = PythonExecutionResult(
                sandbox_id=sandbox_id,
                result=parsed,
                stdout=_decode_log(stdout),
                stderr=_decode_log(stderr),
                output_files=downloaded,
            )
        except BaseException as error:
            primary_error = error
        cleanup_task = asyncio.create_task(self._cleanup(client, sandbox, timeout_seconds))
        try:
            cleanup_error = await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as error:
            primary_error = primary_error or error
            cleanup_error = await asyncio.shield(cleanup_task)
        if cleanup_error is not None:
            cleanup = DaytonaCleanupError(sandbox_id, redact_secrets(str(cleanup_error)))
            if primary_error is not None:
                raise cleanup from primary_error
            raise cleanup from cleanup_error
        if primary_error is not None:
            raise primary_error
        if result is None:
            raise DaytonaExecutionError(sandbox_id, "execution ended without a result")
        return result

    async def _cleanup(
        self, client: Any | None, sandbox: Any | None, timeout_seconds: int
    ) -> BaseException | None:
        failure: BaseException | None = None
        try:
            if client is not None and sandbox is not None:
                await asyncio.wait_for(
                    client.delete(sandbox, timeout=timeout_seconds, wait=True),
                    timeout=timeout_seconds,
                )
        except BaseException as error:
            failure = error
        finally:
            if client is not None:
                try:
                    await asyncio.wait_for(client.close(), timeout=_SDK_REQUEST_TIMEOUT_SECONDS)
                except BaseException as error:
                    failure = failure or error
        return failure

    def _sdk(self) -> Any:
        try:
            import daytona
        except ImportError as error:
            raise DaytonaConfigurationError(
                "Daytona execution requires the daytona package"
            ) from error
        return daytona

    def _sdk_config(self, daytona: Any) -> Any:
        if not self._settings.api_key:
            raise DaytonaConfigurationError("Daytona API key is required")
        values: dict[str, str] = {"api_key": self._settings.api_key}
        if self._settings.api_url:
            values["api_url"] = self._settings.api_url
        if self._settings.target:
            values["target"] = self._settings.target
        try:
            return daytona.DaytonaConfig(**values)
        except Exception as error:
            raise DaytonaConfigurationError("invalid Daytona client configuration") from error

    def _create_params(self, daytona: Any) -> Any:
        values: dict[str, Any] = {
            "language": "python",
            "auto_stop_interval": 0,
            "ttl_minutes": self._provision_ttl_minutes(),
            "network_block_all": self._settings.network_block_all,
        }
        if self._settings.snapshot:
            values["snapshot"] = self._settings.snapshot
        if self._settings.target:
            values["target"] = self._settings.target
        try:
            return daytona.CreateSandboxFromSnapshotParams(**values)
        except Exception as error:
            raise DaytonaConfigurationError("invalid Daytona sandbox configuration") from error

    @staticmethod
    def _provision_ttl_minutes() -> int:
        setup_seconds = (
            _MAX_TIMEOUT_SECONDS
            + 3 * _SDK_REQUEST_TIMEOUT_SECONDS
            + _MAX_UPLOAD_COUNT * _SDK_REQUEST_TIMEOUT_SECONDS
        )
        return max(2, math.ceil((setup_seconds + 60) / 60))

    @staticmethod
    def _execution_ttl_minutes(timeout_seconds: int) -> int:
        export_seconds = _MAX_DOWNLOAD_COUNT * _SDK_REQUEST_TIMEOUT_SECONDS
        deletion_seconds = timeout_seconds
        return max(
            2,
            math.ceil((timeout_seconds + export_seconds + deletion_seconds + 60) / 60),
        )

    @staticmethod
    def _verify_resource_policy(sandbox: Any) -> None:
        resources: dict[str, Any] = {
            "cpu": getattr(sandbox, "cpu", None),
            "memory": getattr(sandbox, "memory", None),
            "disk": getattr(sandbox, "disk", None),
            "gpu": getattr(sandbox, "gpu", None),
        }
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in resources.values()
        ):
            raise DaytonaConfigurationError("Daytona did not report finite sandbox resource limits")
        if (
            resources["cpu"] <= 0
            or resources["memory"] <= 0
            or resources["disk"] <= 0
            or resources["gpu"] < 0
        ):
            raise DaytonaConfigurationError("Daytona reported invalid sandbox resource limits")
        if (
            resources["cpu"] > _MAX_SANDBOX_CPU
            or resources["memory"] > _MAX_SANDBOX_MEMORY_GIB
            or resources["disk"] > _MAX_SANDBOX_DISK_GIB
            or resources["gpu"] > 0
        ):
            raise DaytonaConfigurationError(
                "Daytona snapshot resources exceed the Python execution policy"
            )

    async def _verify_network_policy(self, sandbox: Any) -> None:
        if not self._settings.network_block_all:
            return
        try:
            await sandbox.refresh_data(request_timeout=_SDK_REQUEST_TIMEOUT_SECONDS)
        except Exception as error:
            raise DaytonaConfigurationError(
                "Daytona could not verify the reported network_block_all setting"
            ) from error
        if getattr(sandbox, "network_block_all", None) is not True:
            raise DaytonaConfigurationError(
                "Daytona did not report network_block_all; this provider flag does not prove "
                "packet-level network enforcement"
            )

    @staticmethod
    async def _prepare_workdir(sandbox: Any, input_files: dict[str, bytes]) -> str:
        parents = sorted(
            {posixpath.dirname(path) for path in input_files if posixpath.dirname(path)}
        )
        setup = (
            "import json,os,sys,tempfile; "
            "root=tempfile.mkdtemp(prefix='progena-'); "
            "[os.makedirs(os.path.join(root,p),mode=0o700,exist_ok=True) "
            "for p in json.loads(sys.argv[1])]; print(root)"
        )
        command = "python3 -c " + shlex.quote(setup) + " " + shlex.quote(json.dumps(parents))
        try:
            response = await sandbox.process.exec(command, timeout=_SDK_REQUEST_TIMEOUT_SECONDS)
        except Exception as error:
            raise DaytonaTransferError("Daytona sandbox work directory setup failed") from error
        path = getattr(response, "result", None)
        if getattr(response, "exit_code", None) != 0 or not isinstance(path, str):
            raise DaytonaTransferError("Daytona sandbox work directory setup failed")
        path = path.strip()
        if (
            not path.startswith("/")
            or posixpath.normpath(path) != path
            or not posixpath.basename(path).startswith("progena-")
            or len(path) > 4096
            or any(character in path for character in "\x00\n\r")
        ):
            raise DaytonaTransferError("Daytona returned an invalid sandbox work directory")
        return path

    @staticmethod
    async def _download_limited(sandbox: Any, path: str, maximum: int) -> bytes:
        try:
            async with asyncio.timeout(_DOWNLOAD_TOTAL_TIMEOUT_SECONDS):
                stream = await sandbox.fs.download_file_stream(
                    path,
                    timeout=_SDK_REQUEST_TIMEOUT_SECONDS,
                )
                try:
                    content = bytearray()
                    async for chunk in stream:
                        if not isinstance(chunk, bytes):
                            raise DaytonaTransferError(
                                f"Daytona returned non-byte content for {path}"
                            )
                        content.extend(chunk)
                        if len(content) > maximum:
                            raise DaytonaTransferError(
                                f"Daytona file {path} exceeds {maximum} bytes"
                            )
                    return bytes(content)
                finally:
                    close = getattr(stream, "aclose", None)
                    if close is not None:
                        await asyncio.wait_for(close(), timeout=_SDK_REQUEST_TIMEOUT_SECONDS)
        except DaytonaTransferError:
            raise
        except TimeoutError as error:
            raise DaytonaTransferError(
                f"Daytona download exceeded {_DOWNLOAD_TOTAL_TIMEOUT_SECONDS} seconds"
            ) from error
        except Exception as error:
            raise DaytonaTransferError(f"Daytona download failed for {path}") from error

    @staticmethod
    def _parse_result(content: bytes, sandbox_id: str) -> Any:
        try:
            parsed = json.loads(
                content.decode("utf-8"),
                parse_constant=_reject_non_finite_json,
            )
            _validate_json(parsed, name="result")
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            DaytonaConfigurationError,
        ) as error:
            raise DaytonaExecutionError(sandbox_id, "result was not finite JSON") from error
        return parsed
