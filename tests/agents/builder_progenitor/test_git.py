"""Tests for builder_progenitor git helpers."""

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
