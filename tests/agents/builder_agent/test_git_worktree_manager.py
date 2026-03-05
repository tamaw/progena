from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.agents.builder_agent.tools.git import GitWorktreeManager


@pytest.mark.asyncio
async def test_create_worktree_reuses_existing_branch_when_present(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)

    manager._run_git = AsyncMock(side_effect=[  # type: ignore[method-assign]
        {"returncode": 0, "stdout": "", "stderr": ""},
    ])

    path = await manager.create_worktree("item-1", branch_name="docs/update-readme")
    assert path.parent == tmp_path / "worktrees"
    assert manager._run_git.await_count == 1  # type: ignore[attr-defined]

    first_args = manager._run_git.await_args_list[0].args[0]  # type: ignore[attr-defined]
    assert first_args[:2] == ["worktree", "add"]
    assert first_args[3] == "docs/update-readme"


@pytest.mark.asyncio
async def test_create_worktree_creates_named_branch_when_missing(tmp_path: Path) -> None:
    manager = GitWorktreeManager(worktrees_dir=tmp_path / "worktrees", repo_path=tmp_path, cleanup_on_init=False)
    manager._run_git = AsyncMock(side_effect=[  # type: ignore[method-assign]
        {"returncode": 128, "stdout": "", "stderr": "fatal: invalid reference: docs/update-readme"},
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
    manager._run_git = AsyncMock(return_value={  # type: ignore[method-assign]
        "returncode": 1,
        "stdout": "",
        "stderr": "fatal: permission denied",
    })

    with pytest.raises(RuntimeError, match="permission denied"):
        await manager.create_worktree("item-2", branch_name="docs/update-readme")
