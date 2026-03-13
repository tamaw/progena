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
from app.agents.builder_progenitor.tools import _derive_work_item_suffix, create_builder_tools


def test_derive_work_item_suffix_prefers_a2a_task_id() -> None:
    context = {"a2a_task_id": "9e286e23-0264-4dad-a43f-7619c41053d3"}
    suffix = _derive_work_item_suffix(json.dumps(context))
    assert suffix.startswith("9e286e2302644dad")


def test_derive_work_item_suffix_without_context_is_manual() -> None:
    suffix = _derive_work_item_suffix(None)
    assert suffix == "manual"


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
