import subprocess
from pathlib import Path

import httpx
import pytest

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.repo_manager import RepoManager


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def _origin(tmp_path: Path) -> Path:
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    source = tmp_path / "source"
    _git(tmp_path, "clone", str(remote), str(source))
    _git(source, "config", "user.name", "Test")
    _git(source, "config", "user.email", "test@example.com")
    (source / "README.md").write_text("initial\n")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial")
    _git(source, "push", "origin", "main")
    return remote


def _manager(
    tmp_path: Path, *, access: str = "write"
) -> tuple[RepoManager, InMemoryDocumentStore, Path]:
    store = InMemoryDocumentStore()
    root = tmp_path / "repos"
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "repo:sample",
        {
            "name": "Sample",
            "local_path": "sample",
            "access": access,
            "remote_url": (tmp_path / "origin.git").as_uri(),
        },
    )
    return RepoManager(store_getter=lambda: store, managed_root=root), store, root


@pytest.mark.asyncio
async def test_sync_worktree_and_remove_are_idempotent(tmp_path: Path):
    _origin(tmp_path)
    manager, _store, root = _manager(tmp_path)

    synced = await manager.execute("sync", {"repo_id": "sample"})
    assert Path(synced["path"]) == root / "sample"
    assert synced["default_branch"] == "main"

    first = await manager.execute(
        "worktree", {"repo_id": "sample", "branch": "feature/card", "base": "origin/main"}
    )
    second = await manager.execute(
        "worktree", {"repo_id": "sample", "branch": "feature/card", "base": "origin/main"}
    )
    assert first["created"] is True
    assert second == {**first, "created": False}
    assert Path(first["path"]).is_dir()

    assert (
        await manager.execute("remove_worktree", {"repo_id": "sample", "branch": "feature/card"})
    )["removed"]
    assert not (
        await manager.execute("remove_worktree", {"repo_id": "sample", "branch": "feature/card"})
    )["removed"]


@pytest.mark.asyncio
async def test_readding_worktree_keeps_unpushed_branch_commits(tmp_path: Path):
    _origin(tmp_path)
    manager, _store, _root = _manager(tmp_path)
    await manager.execute("sync", {"repo_id": "sample"})
    first = await manager.execute("worktree", {"repo_id": "sample", "branch": "feature/card"})
    worktree = Path(first["path"])
    (worktree / "work.txt").write_text("unpushed\n")
    _git(worktree, "add", "work.txt")
    _git(worktree, "commit", "-m", "unpushed work")
    committed = _git(worktree, "rev-parse", "HEAD")
    await manager.execute("remove_worktree", {"repo_id": "sample", "branch": "feature/card"})

    again = await manager.execute("worktree", {"repo_id": "sample", "branch": "feature/card"})

    assert again["created"] is True
    assert again["head"] == committed
    assert (Path(again["path"]) / "work.txt").read_text() == "unpushed\n"


@pytest.mark.asyncio
async def test_write_actions_refuse_read_only_repo(tmp_path: Path):
    _origin(tmp_path)
    manager, _store, _root = _manager(tmp_path, access="read")
    with pytest.raises(ValueError, match="read-only"):
        await manager.execute("worktree", {"repo_id": "sample", "branch": "feature/card"})


@pytest.mark.asyncio
async def test_publish_reuses_open_pull_request_with_mocked_github(tmp_path: Path, monkeypatch):
    _origin(tmp_path)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        if (
            request.method == "GET"
            and len([item for item in requests if item.method == "GET"]) == 1
        ):
            return httpx.Response(
                200, json=[{"html_url": "https://github.com/acme/sample/pull/7", "number": 7}]
            )
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(
            201, json={"html_url": "https://github.com/acme/sample/pull/8", "number": 8}
        )

    manager, _store, root = _manager(tmp_path)
    manager._http_transport = httpx.MockTransport(handler)
    monkeypatch.setenv("GH_TOKEN", "test-token")
    checkout = root / "sample"
    checkout.parent.mkdir(parents=True)
    _git(tmp_path, "clone", str(tmp_path / "origin.git"), str(checkout))
    _git(checkout, "remote", "set-url", "origin", "https://github.com/acme/sample.git")

    async def fake_git(cwd: Path, *args: str, use_credentials: bool = False):
        if args[:2] == ("rev-parse", "--is-inside-work-tree"):
            return "true", ""
        if args[:2] == ("remote", "get-url"):
            return "https://github.com/acme/sample.git", ""
        if args[:2] == ("symbolic-ref", "--short"):
            return "origin/main", ""
        if args[:2] == ("worktree", "list"):
            return f"worktree {checkout}\nbranch refs/heads/feature/card\n", ""
        if args[:2] == ("push", "-u"):
            return "", ""
        raise AssertionError(args)

    manager._git = fake_git  # type: ignore[method-assign]
    result = await manager.publish(
        "sample",
        {"access": "write", "local_path": "sample"},
        branch="feature/card",
        title="Card",
        body="",
    )
    created = await manager.publish(
        "sample",
        {"access": "write", "local_path": "sample"},
        branch="feature/card",
        title="Card",
        body="",
    )
    assert result == {
        "repo_id": "sample",
        "branch": "feature/card",
        "pushed": True,
        "pr_url": "https://github.com/acme/sample/pull/7",
        "pr_number": 7,
    }
    assert created == {**result, "pr_url": "https://github.com/acme/sample/pull/8", "pr_number": 8}
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
