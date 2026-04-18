"""Tests for the Builder router-based service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.builder.agent import BuilderService, BuildOutcome
from app.agents.builder.client import BuildPlan, PlannedBuild
from app.agents.builder.subagents.build.prompts import (
    build_execution_prompt,
    build_setup_prompt,
    build_teardown_failed_prompt,
    build_teardown_prompt,
)


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
    prompt = build_execution_prompt("request", {}, _planned_build())
    assert "render_copilot_prompt" in prompt
    assert "task setup phase already ran" in prompt.lower()
    assert "Before finishing, check your own work" in prompt


def test_setup_prompt_requires_workspace_creation():
    prompt = build_setup_prompt("request", {}, _planned_build())
    assert "create_worktree" in prompt
    assert "origin/main" in prompt
    assert "Do not call `copilot_build`" in prompt


def test_teardown_prompt_requires_cleanup_and_pr():
    prompt = build_teardown_prompt("request", {}, _planned_build(), None)
    assert "create_pull_request" in prompt
    assert "remove_worktree" in prompt


def test_teardown_failed_prompt_forbids_pr_and_push():
    report = {"verdict": "fail", "summary": "tests failed", "gaps": ["pytest red"]}
    prompt = build_teardown_failed_prompt("request", {}, _planned_build(), report)
    assert "Do **not** push" in prompt
    assert "Do **not** call `create_pull_request`" in prompt
    assert "remove_worktree" in prompt


@pytest.mark.asyncio
async def test_builder_service_process_returns_build_outcome():
    """``process`` invokes the router graph and maps the result to BuildOutcome."""
    service = BuilderService()
    planned = _planned_build()
    router_graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "result_text": "Teardown complete",
                "route": "build",
                "planned_build": planned,
                "metadata": {
                    "subagent": "build",
                    "validation": {"verdict": "pass", "score": 1.0},
                    "teardown_path": "success",
                    "commit_hash": "abc1234",
                    "pr_url": "https://github.com/org/repo/pull/9",
                },
            }
        ),
    )
    service._router_graph = router_graph  # type: ignore[assignment]
    service._initialized = True

    outcome = await service.process("request", {"foo": "bar"})

    assert isinstance(outcome, BuildOutcome)
    assert outcome.result_text == "Teardown complete"
    assert outcome.route == "build"
    assert outcome.planned_build is planned
    assert outcome.commit_hash == "abc1234"
    assert outcome.pr_url == "https://github.com/org/repo/pull/9"
    assert outcome.phase_completed == "pr"
    router_graph.ainvoke.assert_awaited_once()
    initial_state = router_graph.ainvoke.await_args.args[0]
    assert initial_state["request_text"] == "request"
    assert initial_state["request_context"] == {"foo": "bar"}
    assert initial_state.get("on_planned") is None


@pytest.mark.asyncio
async def test_builder_service_process_marks_failure_on_validation_fail():
    """A ``fail`` validation verdict must surface as ``validation_failed``."""
    service = BuilderService()
    router_graph = SimpleNamespace(
        ainvoke=AsyncMock(
            return_value={
                "result_text": "Cleanup only",
                "route": "build",
                "metadata": {
                    "subagent": "build",
                    "validation": {"verdict": "fail", "summary": "tests failed"},
                    "teardown_path": "failed",
                },
            }
        ),
    )
    service._router_graph = router_graph  # type: ignore[assignment]
    service._initialized = True

    outcome = await service.process("request", {})

    assert outcome.phase_completed == "validation_failed"
    assert outcome.pr_url is None
    assert outcome.validation_report == {"verdict": "fail", "summary": "tests failed"}


@pytest.mark.asyncio
async def test_builder_service_process_propagates_on_planned_callback():
    service = BuilderService()
    router_graph = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"result_text": "ok"}),
    )
    service._router_graph = router_graph  # type: ignore[assignment]
    service._initialized = True

    async def cb(_: PlannedBuild) -> None:
        return None

    await service.process("request", {}, on_planned=cb)
    initial_state = router_graph.ainvoke.await_args.args[0]
    assert initial_state["on_planned"] is cb
