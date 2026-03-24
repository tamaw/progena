import json
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
            state_dir=tmp_path / "state",
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
        patch("app.agents.builder_progenitor.tools.ToolRuntime.current_branch", new=AsyncMock(return_value="feature/test")),
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
                    "stderr": "a pull request for branch \"feature/test\" into branch \"main\" already exists:\nhttps://github.com/org/repo/pull/42",
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
async def test_render_copilot_prompt_uses_structured_metadata(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "render_copilot_prompt")

    result = await tool.coroutine(
        request_text="Fix the scheduler",
        task_name="Scheduler Fix",
        work_item_id="scheduler-fix",
        branch_name="progenitor/scheduler-fix",
        description="Fix ISO timestamp parsing",
        planned_at_utc="2026-03-23T16:00:00Z",
        context_json=json.dumps({"a2a_task_id": "123"}),
        copilot_goal="Update parser and tests",
        success_criteria="- ISO timestamps survive\n- Tests cover the bug",
        validation_targets="- pytest\n- pyright\n- ruff",
    )

    prompt = result["prompt"]
    assert "Scheduler Fix" in prompt
    assert "progenitor/scheduler-fix" in prompt
    assert "Update parser and tests" in prompt
    assert "Fix the scheduler" in prompt


@pytest.mark.asyncio
async def test_gh_cli_accepts_command_string(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "gh_cli")

    with patch(
        "app.agents.builder_progenitor.tools.ToolRuntime.run_gh",
        new=AsyncMock(return_value={"success": True, "stdout": "ok", "stderr": "", "returncode": 0}),
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
async def test_gh_cli_accepts_legacy_variadic_args_payload(tmp_path: Path) -> None:
    tools = create_builder_tools(config=_test_config(tmp_path), project_root=tmp_path)
    tool = next(tool for tool in tools if tool.name == "gh_cli")

    with patch(
        "app.agents.builder_progenitor.tools.ToolRuntime.run_gh",
        new=AsyncMock(return_value={"success": True, "stdout": "ok", "stderr": "", "returncode": 0}),
    ) as run_gh:
        result = await tool.ainvoke(
            {
                "worktree_path": str(tmp_path),
                "v__args": ["pr", "view", "123"],
            }
        )

    assert result["success"] is True
    run_gh.assert_awaited_once_with(str(tmp_path), ["pr", "view", "123"], timeout=120)
