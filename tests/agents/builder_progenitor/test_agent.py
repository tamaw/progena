"""Tests for Builder agent execution safeguards."""

import pytest

from app.agents.builder_progenitor.agent import (
    _assert_result_has_pr_when_committed,
    _build_prompt,
)
from app.agents.builder_progenitor.client import PlanResponse, PlanStep, ReasonedPlan


def _reasoned_plan() -> ReasonedPlan:
    return ReasonedPlan(
        plan=PlanResponse(
            summary="s",
            steps=[
                PlanStep(step="plan", objective="inspect", commands=[]),
                PlanStep(step="pr", objective="open pr", commands=["gh pr create"]),
            ],
            risks=[],
        ),
        reasoning="r",
    )


def test_build_prompt_contains_explicit_pr_requirements():
    prompt = _build_prompt("request", {}, _reasoned_plan())
    assert "create_pull_request" in prompt
    assert "Do not finish until create_pull_request succeeds" in prompt


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


def test_assert_result_raises_when_pr_call_failed():
    result = {
        "messages": [
            {"tool_name": "create_pull_request", "content": '{"success": false, "error": "Push failed"}'}
        ]
    }
    with pytest.raises(RuntimeError, match="create_pull_request returned success=false"):
        _assert_result_has_pr_when_committed(result)
