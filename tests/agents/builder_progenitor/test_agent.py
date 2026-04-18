"""Tests for Builder agent execution safeguards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.builder_progenitor.agent import (
    BuilderService,
    _assert_result_has_pr_when_committed,
    _assert_setup_ready,
    _assert_teardown_complete,
    _build_execution_prompt,
    _build_setup_prompt,
    _build_teardown_prompt,
)
from app.agents.builder_progenitor.client import BuildPlan, PlannedBuild


def _planned_build() -> PlannedBuild:
    return PlannedBuild(
        plan=BuildPlan(
            task_name="Scheduler Fix",
            work_item_id="scheduler-fix",
            branch_name="progenitor/scheduler-fix",
            description="Fix scheduler timestamp parsing",
            copilot_goal="Update parser and tests",
            success_criteria=["ISO timestamps are preserved"],
            validation_targets=["pytest", "pyright", "ruff"],
        ),
        reasoning="Use the smallest safe change.",
        planned_at_utc="2026-03-23T16:00:00Z",
    )


def test_execution_prompt_contains_explicit_pr_requirements():
    prompt = _build_execution_prompt("request", {}, _planned_build())
    assert "render_copilot_prompt" in prompt
    assert "task setup phase already ran" in prompt.lower()
    assert "Before finishing, check your own work" in prompt


def test_setup_prompt_requires_workspace_creation():
    prompt = _build_setup_prompt("request", {}, _planned_build())
    assert "create_worktree" in prompt
    assert "origin/main" in prompt
    assert "Do not call `copilot_build`" in prompt


def test_assert_setup_ready_raises_when_tool_missing():
    with pytest.raises(RuntimeError, match="create_worktree was not called"):
        _assert_setup_ready({"messages": [{"content": "No tools used"}]})


def test_assert_setup_ready_accepts_created_worktree():
    result = {
        "messages": [
            {
                "tool_name": "create_worktree",
                "content": '{"worktree_path": "/tmp/worktree", "branch": "feature/test"}',
            }
        ]
    }

    _assert_setup_ready(result)


def test_teardown_prompt_requires_cleanup_and_pr():
    prompt = _build_teardown_prompt("request", {}, _planned_build())
    assert "create_pull_request" in prompt
    assert "remove_worktree" in prompt


def test_assert_teardown_complete_requires_cleanup():
    with pytest.raises(RuntimeError, match="remove_worktree was not called"):
        _assert_teardown_complete({"messages": [{"content": "no commit"}]}, {"messages": []})


def test_assert_teardown_complete_requires_pr_after_commit():
    execution_result = {
        "messages": [
            {"tool_name": "commit_changes", "content": '{"committed": true, "sha": "abc"}'},
        ]
    }
    teardown_result = {
        "messages": [
            {"tool_name": "remove_worktree", "content": '{"success": true, "message": "Removed"}'},
        ]
    }

    with pytest.raises(RuntimeError, match="did not produce a pull request URL"):
        _assert_teardown_complete(execution_result, teardown_result)


@pytest.mark.asyncio
async def test_builder_service_runs_setup_execution_and_teardown(
    monkeypatch: pytest.MonkeyPatch,
):
    service = BuilderService()
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=[
                {
                    "messages": [
                        {
                            "tool_name": "create_worktree",
                            "content": '{"worktree_path": "/tmp/wt", "branch": "feature/test"}',
                        }
                    ]
                },
                {
                    "messages": [
                        {
                            "tool_name": "commit_changes",
                            "content": '{"committed": true, "sha": "abc"}',
                        },
                        {"content": "Build complete"},
                    ]
                },
                {
                    "messages": [
                        {
                            "tool_name": "commit_changes",
                            "content": '{"committed": true, "sha": "abc"}',
                        },
                        {
                            "tool_name": "create_pull_request",
                            "content": '{"success": true, "url": "https://github.com/org/repo/pull/123"}',
                        },
                        {
                            "tool_name": "remove_worktree",
                            "content": '{"success": true, "message": "Removed"}',
                        },
                        {"content": "Teardown complete"},
                    ]
                },
            ]
        )
    )
    service._graph = graph
    service._initialized = True

    monkeypatch.setattr(
        "app.agents.builder_progenitor.agent.uuid.uuid4",
        lambda: SimpleNamespace(hex="abcdef1234567890"),
    )

    result = await service.process("request", {}, _planned_build())

    assert result == "Teardown complete"
    assert graph.ainvoke.await_count == 3
    first_call = graph.ainvoke.await_args_list[0]
    second_call = graph.ainvoke.await_args_list[1]
    third_call = graph.ainvoke.await_args_list[2]
    assert "task setup phase only" in first_call.args[0]["messages"][0]["content"].lower()
    assert "execution requirements" in second_call.args[0]["messages"][0]["content"].lower()
    assert "teardown requirements" in third_call.args[0]["messages"][0]["content"].lower()
    assert first_call.args[1] == second_call.args[1] == third_call.args[1]


def test_assert_result_raises_when_commit_without_pr_url():
    result = {
        "messages": [
            {"tool_name": "commit_changes", "content": '{"committed": true, "sha": "abc"}'},
        ]
    }
    with pytest.raises(RuntimeError, match="did not produce a pull request URL"):
        _assert_result_has_pr_when_committed(result)


def test_assert_result_accepts_commit_with_pr_url():
    result = {
        "messages": [
            {"tool_name": "commit_changes", "content": '{"committed": true, "sha": "abc"}'},
            {
                "tool_name": "create_pull_request",
                "content": '{"success": true, "url": "https://github.com/org/repo/pull/123"}',
            },
        ]
    }
    _assert_result_has_pr_when_committed(result)


def test_assert_result_accepts_existing_pr_update_with_pr_url():
    result = {
        "messages": [
            {"tool_name": "commit_changes", "content": '{"committed": true, "sha": "abc"}'},
            {
                "tool_name": "create_pull_request",
                "content": '{"success": true, "pushed": true, "existing_pr": true, "url": "https://github.com/org/repo/pull/123"}',
            },
        ]
    }
    _assert_result_has_pr_when_committed(result)


def test_assert_result_raises_when_pr_call_failed():
    result = {
        "messages": [
            {
                "tool_name": "create_pull_request",
                "content": '{"success": false, "error": "Push failed"}',
            }
        ]
    }
    with pytest.raises(RuntimeError, match="create_pull_request returned success=false"):
        _assert_result_has_pr_when_committed(result)


def test_assert_result_ignores_unrelated_success_false_values():
    result = {
        "messages": [
            {"tool_name": "commit_changes", "content": '{"committed": true, "sha": "abc"}'},
            {
                "tool_name": "validate_code",
                "content": '{"pytest": {"passed": true}, "pyright": {"passed": false}}',
            },
            {
                "tool_name": "create_pull_request",
                "content": '{"success": true, "url": "https://github.com/org/repo/pull/123"}',
            },
            {
                "content": 'Final report with example JSON {"success": false, "error": "unrelated"}',
            },
        ]
    }

    _assert_result_has_pr_when_committed(result)
