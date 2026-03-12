import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.builder_agent.tools.git import GitWorktreeManager


@pytest.mark.asyncio
async def test_create_worktree_reuses_existing_branch_when_present(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)

    manager._run_git = AsyncMock(side_effect=[  # type: ignore[method-assign]
        {"returncode": 0, "stdout": "", "stderr": ""},
        {"returncode": 0, "stdout": "", "stderr": ""},
    ])

    path = await manager.create_worktree("item-1", branch_name="docs/update-readme")
    assert path.parent == tmp_path / "worktrees"
    assert manager._run_git.await_count == 2  # type: ignore[attr-defined]

    check_args = manager._run_git.await_args_list[0].args[0]  # type: ignore[attr-defined]
    add_args = manager._run_git.await_args_list[1].args[0]  # type: ignore[attr-defined]
    assert check_args[:2] == ["show-ref", "--verify"]
    assert add_args[:2] == ["worktree", "add"]
    assert add_args[3] == "docs/update-readme"


@pytest.mark.asyncio
async def test_create_worktree_creates_named_branch_when_missing(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)
    manager._run_git = AsyncMock(side_effect=[  # type: ignore[method-assign]
        {"returncode": 1, "stdout": "", "stderr": "fatal: not found"},
        {"returncode": 0, "stdout": "", "stderr": ""},
    ])

    await manager.create_worktree("item-2", branch_name="docs/update-readme")
    assert manager._run_git.await_count == 2  # type: ignore[attr-defined]
    second_args = manager._run_git.await_args_list[1].args[0]  # type: ignore[attr-defined]
    assert second_args[:3] == ["worktree", "add", "-b"]
    assert second_args[3] == "docs/update-readme"


@pytest.mark.asyncio
async def test_create_worktree_raises_for_non_branch_error(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)

    # Mock git to return a different error code (not 0, 1, or 128) to trigger error
    manager._run_git = AsyncMock(return_value={  # type: ignore[method-assign]
        "returncode": 2,  # Different fatal error, not "not a valid ref"
        "stdout": "",
        "stderr": "fatal: permissions error while checking ref",
    })

    with pytest.raises(RuntimeError, match="Failed to check branch"):
        await manager.create_worktree("item-2", branch_name="docs/update-readme")


@pytest.mark.asyncio
async def test_commit_changes_raises_when_commit_does_not_advance_head(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)
    manager.prepare_git_for_worktree = AsyncMock()  # type: ignore[method-assign]
    manager._run_git = AsyncMock(side_effect=[  # type: ignore[method-assign]
        {"returncode": 0, "stdout": "", "stderr": ""},  # add -A
        {"returncode": 0, "stdout": "M file.py", "stderr": ""},  # status --porcelain
        {"returncode": 0, "stdout": "abc123\n", "stderr": ""},  # rev-parse before
        {"returncode": 0, "stdout": "", "stderr": ""},  # commit
        {"returncode": 0, "stdout": "abc123\n", "stderr": ""},  # rev-parse after
    ])

    with pytest.raises(RuntimeError, match="without advancing HEAD"):
        await manager.commit_changes(tmp_path, "commit message")


@pytest.mark.asyncio
async def test_run_git_kills_process_on_cancellation(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)
    process = AsyncMock()
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    process.kill = MagicMock()

    with (
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)),
        pytest.raises(asyncio.CancelledError),
    ):
        await manager._run_git(["status"], cwd=tmp_path)

    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


@pytest.mark.asyncio
async def test_commit_changes_prepares_git_for_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)
    monkeypatch.setenv("GH_TOKEN", "token")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Builder Bot")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "builder@example.com")
    rev_parse_calls = 0

    async def fake_run_git(args: list[str], cwd: Path, timeout: float = 30.0) -> dict[str, str | int]:
        nonlocal rev_parse_calls
        if args == ["status", "--porcelain"]:
            return {"returncode": 0, "stdout": "M agent.py\n", "stderr": ""}
        if args == ["rev-parse", "HEAD"]:
            rev_parse_calls += 1
            sha = "abc123" if rev_parse_calls == 1 else "def456"
            return {"returncode": 0, "stdout": f"{sha}\n", "stderr": ""}
        return {"returncode": 0, "stdout": "", "stderr": ""}

    manager._run_git = AsyncMock(side_effect=fake_run_git)  # type: ignore[method-assign]

    sha = await manager.commit_changes(tmp_path / "worktree", "test commit")

    assert sha == "def456"
    calls = [call.args[0] for call in manager._run_git.await_args_list]  # type: ignore[attr-defined]
    assert ["config", "user.name", "Builder Bot"] in calls
    assert ["config", "user.email", "builder@example.com"] in calls
    assert ["config", "credential.helper", "!gh auth git-credential"] in calls
