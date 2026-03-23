from unittest.mock import AsyncMock, patch

import pytest

from app.agents.builder_agent.nodes.publish import create_node


@pytest.mark.asyncio
async def test_publish_skips_pr_when_nothing_to_commit(tmp_path):
    state_manager = AsyncMock()
    state = {
        "current_work_item": {
            "id": "item-1",
            "worktree_path": str(tmp_path / "wt"),
            "item_type": "build_agent",
            "description": "Build the agent",
            "context": {},
        },
        "build_settings": {"project_name": "demo_agent"},
        "verification": {"current_passed": True, "current_score": 0.9, "target_score": 0.8},
        "build_messages": [],
    }

    with patch("app.agents.builder_agent.nodes.publish.GitWorktreeManager") as git_cls:
        git_cls.return_value.commit_changes = AsyncMock(return_value=None)
        node = create_node(state_manager, tmp_path)

        with patch("app.agents.builder_agent.nodes.publish.gh_create_pr_from_worktree", new_callable=AsyncMock) as create_pr:
            result = await node(state)

    create_pr.assert_not_awaited()
    assert result["build_messages"][-1]["pr_status"] == "skipped"
    assert result["build_messages"][-1]["reason"] == "no_changes_to_commit"


@pytest.mark.asyncio
async def test_publish_records_failure_when_pr_creation_fails_after_commit(tmp_path):
    state_manager = AsyncMock()
    state = {
        "current_work_item": {
            "id": "item-2",
            "worktree_path": str(tmp_path / "wt"),
            "item_type": "build_agent",
            "description": "Build the agent",
            "context": {},
        },
        "build_settings": {"project_name": "demo_agent"},
        "verification": {"current_passed": True, "current_score": 0.9, "target_score": 0.8},
        "build_messages": [],
    }

    with patch("app.agents.builder_agent.nodes.publish.GitWorktreeManager") as git_cls:
        git_cls.return_value.commit_changes = AsyncMock(return_value="abc1234")
        node = create_node(state_manager, tmp_path)

        with patch(
            "app.agents.builder_agent.nodes.publish.gh_create_pr_from_worktree",
            new_callable=AsyncMock,
            return_value=(False, "push failed"),
        ):
            result = await node(state)

    assert "PR creation failed after commit abc1234" in result["build_messages"][-1]["error"]
    logged_events = [call.args[0]["event"] for call in state_manager.log_decision.await_args_list]
    assert "pr_failed" in logged_events


@pytest.mark.asyncio
async def test_publish_records_commit_hash_and_pr_url_on_success(tmp_path):
    state_manager = AsyncMock()
    state = {
        "current_work_item": {
            "id": "item-3",
            "worktree_path": str(tmp_path / "wt"),
            "item_type": "build_agent",
            "description": "Build the agent",
            "context": {},
        },
        "build_settings": {"project_name": "demo_agent"},
        "verification": {"current_passed": True, "current_score": 0.9, "target_score": 0.8},
        "build_messages": [],
    }

    with patch("app.agents.builder_agent.nodes.publish.GitWorktreeManager") as git_cls:
        git_cls.return_value.commit_changes = AsyncMock(return_value="abc1234")
        node = create_node(state_manager, tmp_path)

        with patch(
            "app.agents.builder_agent.nodes.publish.gh_create_pr_from_worktree",
            new_callable=AsyncMock,
            return_value=(True, "https://github.com/org/repo/pull/42"),
        ):
            result = await node(state)

    current_item = result["current_work_item"]
    assert current_item["context"]["commit_hash"] == "abc1234"
    assert current_item["context"]["pr_url"] == "https://github.com/org/repo/pull/42"
    assert result["build_messages"][-1]["commit_hash"] == "abc1234"
