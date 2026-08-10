import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GIT_SHIM = ROOT / "data" / "shims" / "bin" / "git"
GH_SHIM = ROOT / "data" / "shims" / "bin" / "gh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not Path("/usr/bin/git").exists(),
    reason="the git shim requires /usr/bin/git",
)


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PROGENITOR_SHIM_SYSTEM_PATH"] = "/usr/bin:/bin"
    return environment


def _run(shim: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(shim), *args],
        cwd=cwd,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


def _seed_repository(path: Path) -> None:
    for arguments in (
        ("init",),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Shell Shim Test"),
    ):
        subprocess.run(["git", *arguments], cwd=path, check=True, capture_output=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=path, check=True, capture_output=True)


def test_git_shim_allows_read_only_status(tmp_path: Path):
    _seed_repository(tmp_path)

    result = _run(GIT_SHIM, "-C", str(tmp_path), "status", "--porcelain=v1")

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "arguments",
    [
        ("push",),
        ("reset", "--hard"),
        ("clean", "-fd"),
        ("checkout", "-b", "branch"),
        ("-c", "core.pager=x", "status"),
        ("--git-dir=/etc", "status"),
    ],
)
def test_git_shim_refuses_mutating_or_unsafe_commands(tmp_path: Path, arguments: tuple[str, ...]):
    _seed_repository(tmp_path)

    result = _run(GIT_SHIM, "-C", str(tmp_path), *arguments)

    assert result.returncode == 2
    assert "progenitor-shim: refused:" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [("pr", "merge", "1"), ("api", "/user"), ("auth", "token")],
)
def test_gh_shim_refuses_mutating_or_sensitive_commands(arguments: tuple[str, ...]):
    result = _run(GH_SHIM, *arguments)

    assert result.returncode == 2
    assert "progenitor-shim: refused:" in result.stderr
