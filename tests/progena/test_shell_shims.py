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


def _environment(*, write: bool = False, publish: bool = False) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PROGENA_SHIM_SYSTEM_PATH"] = "/usr/bin:/bin"
    environment.pop("PROGENA_SHIM_GIT_WRITE", None)
    environment.pop("PROGENA_SHIM_GIT_PUBLISH", None)
    environment.pop("GH_TOKEN", None)
    if write:
        environment["PROGENA_SHIM_GIT_WRITE"] = "1"
    if publish:
        environment["PROGENA_SHIM_GIT_PUBLISH"] = "1"
    return environment


def _run(
    shim: Path,
    *args: str,
    cwd: Path | None = None,
    write: bool = False,
    publish: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(shim), *args],
        cwd=cwd,
        env=_environment(write=write, publish=publish),
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


@pytest.mark.parametrize("options", [(), ("--short",), ("-q",), ("--no-recurse",)])
def test_git_shim_allows_symbolic_ref_reads(tmp_path: Path, options: tuple[str, ...]):
    _seed_repository(tmp_path)
    result = _run(GIT_SHIM, "-C", str(tmp_path), "symbolic-ref", *options, "HEAD")
    assert result.returncode == 0
    assert result.stdout.strip()


@pytest.mark.parametrize(
    "arguments",
    [
        ("HEAD", "refs/heads/changed"),
        ("--delete", "HEAD"),
        ("-d", "HEAD"),
        ("-m", "message", "HEAD", "refs/heads/changed"),
        ("--short", "HEAD", "refs/heads/changed"),
    ],
)
def test_git_shim_refuses_symbolic_ref_writes(tmp_path: Path, arguments: tuple[str, ...]):
    _seed_repository(tmp_path)
    head_before = (tmp_path / ".git" / "HEAD").read_bytes()
    result = _run(GIT_SHIM, "-C", str(tmp_path), "symbolic-ref", *arguments)
    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr
    assert (tmp_path / ".git" / "HEAD").read_bytes() == head_before


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
    assert "progena-shim: refused:" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [("pr", "merge", "1"), ("api", "/user"), ("auth", "token")],
)
def test_gh_shim_refuses_mutating_or_sensitive_commands(arguments: tuple[str, ...]):
    result = _run(GH_SHIM, *arguments)

    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr


@pytest.mark.parametrize("flag", ["--hostname=evil.com", "--method=POST", "--template={{.}}"])
def test_gh_shim_refuses_equals_form(flag: str) -> None:
    result = _run(GH_SHIM, "auth", "status", flag)
    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr


def test_setup_commands_need_git_write(tmp_path: Path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _seed_repository(origin)

    refused = _run(GIT_SHIM, "clone", str(origin), str(tmp_path / "read-only"))
    assert refused.returncode == 2
    assert not (tmp_path / "read-only").exists()
    gh_refused = _run(GH_SHIM, "repo", "clone", "org/repo")
    assert gh_refused.returncode == 2

    clone = tmp_path / "clone"
    assert _run(GIT_SHIM, "clone", str(origin), str(clone), write=True).returncode == 0
    for arguments in (
        ("fetch",),
        ("pull", "--ff-only"),
        ("switch", "-c", "feature"),
        ("worktree", "add", str(tmp_path / "wt"), "-b", "wt-branch"),
        ("branch", "spare"),
    ):
        result = _run(GIT_SHIM, "-C", str(clone), *arguments, write=True)
        assert result.returncode == 0, (arguments, result.stderr)
    assert (tmp_path / "wt" / "tracked.txt").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ("push",),
        ("commit", "-m", "x"),
        ("reset", "--hard"),
        ("checkout", "tracked.txt"),
        ("pull",),
        ("pull", "--ff-only", "--rebase"),
        ("switch", "--discard-changes", "master"),
        ("branch", "-D", "master"),
        ("worktree", "remove", "--force", "x"),
        ("clean", "-fd"),
    ],
)
def test_git_write_still_refuses_work_losing_or_publishing_commands(
    tmp_path: Path, arguments: tuple[str, ...]
):
    _seed_repository(tmp_path)
    (tmp_path / "tracked.txt").write_text("local edit\n", encoding="utf-8")

    result = _run(GIT_SHIM, "-C", str(tmp_path), *arguments, write=True)

    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "local edit\n"


def _published_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin and a clone of it on a fresh feature branch."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _seed_repository(seed)
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)], check=True, capture_output=True
    )
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    subprocess.run(
        ["git", "switch", "-c", "feature/card"], cwd=clone, check=True, capture_output=True
    )
    return origin, clone


def test_publish_pushes_a_named_feature_branch(tmp_path: Path):
    origin, clone = _published_clone(tmp_path)

    refused = _run(GIT_SHIM, "-C", str(clone), "push", "-u", "origin", "feature/card", write=True)
    assert refused.returncode == 2

    pushed = _run(
        GIT_SHIM, "-C", str(clone), "push", "-u", "origin", "feature/card", write=True, publish=True
    )
    assert pushed.returncode == 0, pushed.stderr
    branches = subprocess.run(
        ["git", "-C", str(origin), "branch", "--list", "feature/card"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "feature/card" in branches.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("push",),
        ("push", "origin"),
        ("push", "origin", "master"),
        ("push", "origin", "main"),
        ("push", "origin", "HEAD"),
        ("push", "origin", "feature/card:master"),
        ("push", "origin", "+feature/card"),
        ("push", "-f", "origin", "feature/card"),
        ("push", "--force-with-lease", "origin", "feature/card"),
        ("push", "--delete", "origin", "feature/card"),
        ("push", "--all", "origin"),
        ("commit", "-m", "x"),
    ],
)
def test_publish_still_refuses_default_branch_forced_or_implicit_pushes(
    tmp_path: Path, arguments: tuple[str, ...]
):
    origin, clone = _published_clone(tmp_path)
    refs_before = sorted((origin / "refs" / "heads").rglob("*"))

    result = _run(GIT_SHIM, "-C", str(clone), *arguments, write=True, publish=True)

    assert result.returncode == 2
    assert "progena-shim: refused:" in result.stderr
    assert sorted((origin / "refs" / "heads").rglob("*")) == refs_before


def test_gh_pr_create_needs_publish():
    for write in (False, True):
        result = _run(GH_SHIM, "pr", "create", "--title", "x", write=write)
        assert result.returncode == 2
        assert "progena-shim: refused:" in result.stderr


def test_worktree_remove_needs_write_and_keeps_uncommitted_work(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_repository(repo)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt", str(worktree)],
        check=True,
        capture_output=True,
    )
    assert _run(GIT_SHIM, "-C", str(repo), "worktree", "remove", str(worktree)).returncode == 2

    (worktree / "tracked.txt").write_text("unsaved\n", encoding="utf-8")
    dirty = _run(GIT_SHIM, "-C", str(repo), "worktree", "remove", str(worktree), write=True)
    assert dirty.returncode != 0
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "unsaved\n"

    subprocess.run(["git", "-C", str(worktree), "checkout", "--", "."], check=True)
    clean = _run(GIT_SHIM, "-C", str(repo), "worktree", "remove", str(worktree), write=True)
    assert clean.returncode == 0, clean.stderr
    assert not worktree.exists()
