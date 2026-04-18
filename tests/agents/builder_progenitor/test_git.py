"""Tests for builder_progenitor git helpers."""

from unittest.mock import AsyncMock

import pytest

from app.agents.builder_progenitor.git import GitWorktreeManager


@pytest.mark.asyncio
async def test_commit_changes_raises_when_git_commit_fails(tmp_path):
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path)

    responses = iter(
        [
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "M file.py\n", "stderr": ""},
            {"returncode": 0, "stdout": "abc123\n", "stderr": ""},
            {"returncode": 1, "stdout": "", "stderr": "Author identity unknown"},
        ]
    )

    async def fake_run_git(args, cwd, timeout=30.0):
        del args, cwd, timeout
        return next(responses)

    manager._run_git = fake_run_git  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Author identity unknown"):
        await manager.commit_changes(tmp_path / "wt", "test commit")


@pytest.mark.asyncio
async def test_create_worktree_uses_base_ref_for_new_branch(tmp_path):
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path)

    responses = iter(
        [
            {"returncode": 1, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "", "stderr": ""},
        ]
    )
    calls: list[list[str]] = []

    async def fake_run_git(args, cwd, timeout=30.0):
        del cwd, timeout
        calls.append(args)
        return next(responses)

    manager._run_git = fake_run_git  # type: ignore[method-assign]
    manager._write_metadata = AsyncMock()  # type: ignore[method-assign]

    await manager.create_worktree(
        "task-id", branch_name="progenitor/task-id", base_ref="origin/main"
    )

    assert calls[0] == ["show-ref", "--verify", "refs/heads/progenitor/task-id"]
    assert calls[1][:4] == ["worktree", "add", "-b", "progenitor/task-id"]
    assert calls[1][-1] == "origin/main"
    assert calls[1][4].startswith(str(tmp_path / "worktrees" / "task-id_"))


@pytest.mark.asyncio
async def test_create_worktree_reuses_existing_branch_without_base_ref(tmp_path):
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path)

    responses = iter(
        [
            {"returncode": 0, "stdout": "", "stderr": ""},
            {"returncode": 0, "stdout": "", "stderr": ""},
        ]
    )
    calls: list[list[str]] = []

    async def fake_run_git(args, cwd, timeout=30.0):
        del cwd, timeout
        calls.append(args)
        return next(responses)

    manager._run_git = fake_run_git  # type: ignore[method-assign]
    manager._write_metadata = AsyncMock()  # type: ignore[method-assign]

    async def branch_exists(_: str) -> bool:
        return True

    manager.branch_exists = branch_exists  # type: ignore[method-assign]

    await manager.create_worktree(
        "task-id", branch_name="progenitor/task-id", base_ref="origin/main"
    )

    assert calls[0][:2] == ["worktree", "add"]
    assert calls[0][-1] == "progenitor/task-id"
    assert calls[0][2].startswith(str(tmp_path / "worktrees" / "task-id_"))
