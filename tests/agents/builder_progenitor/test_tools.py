from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.builder_progenitor.config import (
    BuilderConfig,
    CopilotConfig,
    LLMConfig,
    StorageConfig,
)
from app.agents.builder_progenitor.tools import create_builder_tools


def _test_config(tmp_path: Path) -> BuilderConfig:
    return BuilderConfig(
        llm=LLMConfig(),
        storage=StorageConfig(
            data_dir=tmp_path / "data",
            state_dir=tmp_path / "data" / "state",
            log_dir=tmp_path / "logs",
            worktree_dir=tmp_path / "worktrees",
        ),
        copilot=CopilotConfig(allowed_dirs=[str(tmp_path)]),
    )


@pytest.mark.asyncio
async def test_create_pull_request_accepts_existing_pr_update(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "create_pull_request")

    with (
        patch(
            "app.agents.builder_progenitor.tools.ToolRuntime.current_branch",
            new=AsyncMock(return_value="feature/test"),
        ),
        patch(
            "app.agents.builder_progenitor.tools.ToolRuntime.run_command",
            new=AsyncMock(return_value={"success": True, "stdout": "", "stderr": ""}),
        ),
        patch(
            "app.agents.builder_progenitor.tools.ToolRuntime.run_gh",
            new=AsyncMock(
                return_value={
                    "success": False,
                    "stdout": "",
                    "stderr": 'a pull request for branch "feature/test" into branch "main" already exists:\nhttps://github.com/org/repo/pull/42',
                }
            ),
        ),
    ):
        result = await tool.coroutine(
            worktree_path=str(tmp_path),
            title="Update PR",
            body="Body",
            base="main",
        )

    assert result["success"] is True
    assert result["pushed"] is True
    assert result["existing_pr"] is True
    assert result["url"] == "https://github.com/org/repo/pull/42"


@pytest.mark.asyncio
async def test_create_worktree_passes_base_ref_to_manager(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "create_worktree")

    with (
        patch(
            "app.agents.builder_progenitor.tools.GitWorktreeManager.create_worktree",
            new_callable=AsyncMock,
        ) as create_worktree,
        patch(
            "app.agents.builder_progenitor.tools.ToolRuntime.current_branch", new_callable=AsyncMock
        ) as current_branch,
    ):
        create_worktree.return_value = tmp_path / "worktrees" / "task-id_123"
        current_branch.return_value = "feature/test"
        result = await tool.coroutine(
            work_item_id="task-id",
            branch_name="feature/test",
            base_ref="origin/main",
        )

    assert result["worktree_path"].endswith("task-id_123")
    assert result["branch"] == "feature/test"
    assert result["base_ref"] == "origin/main"
    create_worktree.assert_awaited_once_with(
        "task-id",
        branch_name="feature/test",
        base_ref="origin/main",
    )
    current_branch.assert_awaited_once()


@pytest.mark.asyncio
async def test_gh_cli_accepts_command_string(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "gh_cli")

    with patch(
        "app.agents.builder_progenitor.tools.ToolRuntime.run_gh",
        new=AsyncMock(
            return_value={"success": True, "stdout": "ok", "stderr": "", "returncode": 0}
        ),
    ) as run_gh:
        result = await tool.ainvoke(
            {
                "worktree_path": str(tmp_path),
                "command": "pr view 123 --comments",
            }
        )

    assert result["success"] is True
    run_gh.assert_awaited_once_with(str(tmp_path), ["pr", "view", "123", "--comments"], timeout=120)


@pytest.mark.asyncio
async def test_git_cli_runs_command_string(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "git_cli")

    with patch(
        "app.agents.builder_progenitor.tools.ToolRuntime.run_command_args",
        new=AsyncMock(
            return_value={"success": True, "stdout": "ok", "stderr": "", "returncode": 0}
        ),
    ) as run_command_args:
        result = await tool.ainvoke(
            {
                "worktree_path": str(tmp_path),
                "command": "status --short",
            }
        )

    assert result["success"] is True
    run_command_args.assert_awaited_once_with(
        str(tmp_path), ["git", "status", "--short"], timeout=120
    )
