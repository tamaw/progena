"""Regression coverage for virtual-file authority and sandbox result publication."""

from pathlib import Path
from typing import Any, cast

import pytest
from deepagents.backends import CompositeBackend, FilesystemBackend
from pydantic import ValidationError

from src.progena.agent.filesystem.artifacts import ArtifactDirectoryBackend
from src.progena.agent.filesystem.base import BoundedFilesystemBackend
from src.progena.agent.filesystem.mounts import ReadOnlyBackend
from src.progena.agent.object_agents.factory import ScopedMountBackend
from src.progena.config import DaytonaConfig, ProgenaConfig
from src.progena.sandboxes.daytona import DaytonaPythonExecutor, PythonExecutionResult
from src.progena.sandboxes.execution import SandboxPythonRequest, create_sandbox_python_runner
from src.progena.tools.protocol import ToolContext
from src.progena.tools.sandbox_python import SandboxPythonTool


class _Documents:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    async def get_content_document(self, kind: str, path: str) -> str | None:
        return self.rows.get((kind, path))

    async def save_content_document(self, kind: str, path: str, content: str) -> None:
        self.rows[kind, path] = content


class _MustNotProvision:
    async def execute(self, **kwargs: Any) -> PythonExecutionResult:
        raise AssertionError("Invalid authority must fail before provisioning a sandbox")


def _forbidden_executor() -> DaytonaPythonExecutor:
    return cast(DaytonaPythonExecutor, _MustNotProvision())


@pytest.mark.parametrize(
    "fields",
    [
        {"code_file": "/config/providers.json"},
        {"code_file": "/artifacts/../config/providers.json"},
        {"code": "result = 1", "input_files": {"a/../b": "/artifacts/data.csv"}},
        {"code": "result = 1", "input_files": {"a//b": "/artifacts/data.csv"}},
        {"code": "result = 1", "output_files": {"report.txt": "/instructions/owner.md"}},
        {"code": "result = 1", "inputs": {"balance": float("nan")}},
        {"code": "result = 1", "code_file": "/artifacts/program.txt"},
    ],
)
def test_request_rejects_ambiguous_or_privileged_file_authority(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SandboxPythonRequest.model_validate(fields)


async def test_unmounted_source_cannot_fall_through_to_default_filesystem(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "program.txt").write_text("result = 42")
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True), routes={}
    )
    run = create_sandbox_python_runner(_forbidden_executor(), backend)
    with pytest.raises(ValueError, match="No declared virtual mount"):
        await run(SandboxPythonRequest(code_file="/artifacts/program.txt"))


async def test_readonly_output_rejected_without_running_code(tmp_path: Path) -> None:
    documents = _Documents()
    documents.rows["artifact", "report.txt"] = "authoritative prior evidence"
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes={"/artifacts/": ReadOnlyBackend(ArtifactDirectoryBackend(documents))},
    )
    run = create_sandbox_python_runner(_forbidden_executor(), backend)
    with pytest.raises(ValueError, match="read-only"):
        await run(
            SandboxPythonRequest(
                code="result = 1", output_files={"report.txt": "/artifacts/report.txt"}
            )
        )
    assert documents.rows["artifact", "report.txt"] == "authoritative prior evidence"


async def test_explicit_read_policy_is_applied_before_sandbox_creation(tmp_path: Path) -> None:
    documents = _Documents()
    documents.rows["artifact", "restricted.txt"] = "result = 42"
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes={"/artifacts/": ArtifactDirectoryBackend(documents)},
    )
    run = create_sandbox_python_runner(
        _forbidden_executor(), backend, path_policy=lambda operation, path: False
    )
    with pytest.raises(ValueError, match="input permission denied"):
        await run(SandboxPythonRequest(code_file="/artifacts/restricted.txt"))


async def test_invalid_export_does_not_overwrite_an_earlier_valid_destination(
    tmp_path: Path,
) -> None:
    class InvalidExport:
        async def execute(self, **kwargs: Any) -> PythonExecutionResult:
            return PythonExecutionResult(
                sandbox_id="already-deleted",
                result={"verified": True},
                stdout="",
                stderr="",
                output_files={"good.txt": b"replacement", "bad.txt": b"\xff"},
            )

    documents = _Documents()
    documents.rows["artifact", "old.txt"] = "keep prior evidence"
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        routes={"/artifacts/": ArtifactDirectoryBackend(documents)},
    )
    run = create_sandbox_python_runner(cast(DaytonaPythonExecutor, InvalidExport()), backend)
    with pytest.raises(UnicodeDecodeError):
        await run(
            SandboxPythonRequest(
                code="result = 1",
                output_files={"good.txt": "/artifacts/old.txt", "bad.txt": "/artifacts/new.txt"},
            )
        )
    assert documents.rows == {("artifact", "old.txt"): "keep prior evidence"}


async def test_unconfigured_tool_refuses_execution_without_host_fallback() -> None:
    result = await SandboxPythonTool().execute(
        ToolContext(tool_registry=None, llm_client=None, tool_args={"code": "result = 1"})
    )
    assert not result.success
    assert result.error is not None


def test_daytona_key_is_private_in_config_repr_and_public_config() -> None:
    secret = "private-daytona-test-credential"
    config = ProgenaConfig(daytona=DaytonaConfig(api_key=secret))
    assert secret not in repr(config)
    assert secret not in repr(config.to_dict())


@pytest.mark.parametrize("api_url", ["file:///tmp/key", "https://secret@example.com/api"])
def test_daytona_endpoint_rejects_local_file_or_embedded_credentials(api_url: str) -> None:
    config = ProgenaConfig(daytona=DaytonaConfig(api_url=api_url))
    with pytest.raises(ValueError, match="DAYTONA_API_URL"):
        config.validate()


async def test_oversized_shared_import_fails_before_provisioning(tmp_path: Path) -> None:
    with (tmp_path / "large.csv").open("wb") as handle:
        handle.truncate(64 * 1024 * 1024)
    backend = CompositeBackend(
        default=object(),
        routes={"/shared/": BoundedFilesystemBackend(root_dir=tmp_path, virtual_mode=True)},
    )
    run = create_sandbox_python_runner(_forbidden_executor(), backend)
    with pytest.raises(ValueError):
        await run(
            SandboxPythonRequest(
                code="result = None", input_files={"large.csv": "/shared/large.csv"}
            )
        )


async def test_scoped_bounded_shared_read_cannot_reach_sibling_files(tmp_path: Path) -> None:
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "fixture.bin").write_bytes(b"\x00\xff fixture")
    (tmp_path / "fixture.bin").write_bytes(b"private sibling")
    scoped = ScopedMountBackend(
        BoundedFilesystemBackend(root_dir=tmp_path, virtual_mode=True), "/allowed"
    )
    assert await scoped.adownload_file_bounded("/fixture.bin", 100) == b"\x00\xff fixture"
    with pytest.raises(ValueError):
        await scoped.adownload_file_bounded("/../fixture.bin", 100)
