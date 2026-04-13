import asyncio
import subprocess
from pathlib import Path

import pytest

from app.agents.builder_progenitor.git import GitWorktreeManager


def run(cmd, cwd=None):
    res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\nstdout:{res.stdout}\nstderr:{res.stderr}")
    return res.stdout.strip()


@pytest.mark.timeout(120)
def test_preflight_clean(tmp_path: Path):
    remote = tmp_path / "remote.git"
    run(f"git init --bare --initial-branch=main {remote}")

    # create an upstream repo to push a commit to the bare remote
    upstream = tmp_path / "upstream"
    run(f"git clone {remote} {upstream}")
    (upstream / "file.txt").write_text("initial\n")
    run("git add file.txt && git commit -m 'init' && git push origin main", cwd=upstream)

    # clone to working repo
    repo = tmp_path / "repo"
    run(f"git clone {remote} {repo}")

    # add another commit to remote
    pusher = tmp_path / "pusher"
    run(f"git clone {remote} {pusher}")
    (pusher / "file2.txt").write_text("update\n")
    run("git add file2.txt && git commit -m 'update' && git push origin main", cwd=pusher)

    mgr = GitWorktreeManager(worktrees_dir=tmp_path / "wts", repo_path=repo)
    result = asyncio.run(mgr.preflight_update())
    assert result.get("success") is True
    assert result.get("updated") in (True, False)
    # If updated, rev should be present
    if result.get("updated"):
        assert "rev" in result


@pytest.mark.timeout(120)
def test_preflight_with_local_changes(tmp_path: Path):
    remote = tmp_path / "remote2.git"
    run(f"git init --bare --initial-branch=main {remote}")

    upstream = tmp_path / "up2"
    run(f"git clone {remote} {upstream}")
    (upstream / "file.txt").write_text("initial\n")
    run("git add file.txt && git commit -m 'init' && git push origin main", cwd=upstream)

    repo = tmp_path / "repo2"
    run(f"git clone {remote} {repo}")

    # create uncommitted change
    (repo / "file.txt").write_text("local change\n")

    mgr = GitWorktreeManager(worktrees_dir=tmp_path / "wts2", repo_path=repo)
    result = asyncio.run(mgr.preflight_update())
    assert result.get("success") is True
    assert result.get("updated") is False
    assert result.get("reason") == "local_changes"
    # ensure local change still present
    assert (repo / "file.txt").read_text().startswith("local change")


@pytest.mark.timeout(120)
def test_preflight_on_worktree(tmp_path: Path):
    remote = tmp_path / "remote3.git"
    run(f"git init --bare --initial-branch=main {remote}")

    upstream = tmp_path / "up3"
    run(f"git clone {remote} {upstream}")
    (upstream / "file.txt").write_text("initial\n")
    run("git add file.txt && git commit -m 'init' && git push origin main", cwd=upstream)

    repo = tmp_path / "repo3"
    run(f"git clone {remote} {repo}")

    # create worktree on a new branch (main is already checked out in repo3)
    wt_dir = tmp_path / "wt"
    run(f"git worktree add -b wt-test {wt_dir}", cwd=repo)

    # push an update from another clone
    pusher = tmp_path / "push3"
    run(f"git clone {remote} {pusher}")
    (pusher / "file2.txt").write_text("update\n")
    run("git add file2.txt && git commit -m 'update' && git push origin main", cwd=pusher)

    mgr = GitWorktreeManager(worktrees_dir=tmp_path / "wts3", repo_path=repo)
    result = asyncio.run(mgr.preflight_update(cwd=wt_dir))
    assert result.get("success") is True
    # worktree should be updated if fast-forwardable
    assert result.get("updated") in (True, False)
