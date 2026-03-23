"""Tests for Builder agent execution safeguards."""

import pytest

from app.agents.builder_progenitor.agent import (
    _assert_result_has_pr_when_committed,
    _build_execution_prompt,
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
    assert "create_pull_request" in prompt
    assert "render_copilot_prompt" in prompt
    assert "Before finishing, check your own work" in prompt


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
            {"tool_name": "create_pull_request", "content": '{"success": false, "error": "Push failed"}'}
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
