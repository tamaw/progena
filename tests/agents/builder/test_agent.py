"""Tests for the Builder router-based service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.builder.agent import BuilderService
from app.agents.builder.client import BuildPlan, PlannedBuild
from app.agents.builder.subagents.build.prompts import (
    build_execution_prompt,
    build_setup_prompt,
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
    prompt = build_teardown_prompt("request", {}, _planned_build())
    assert "create_pull_request" in prompt
    assert "remove_worktree" in prompt


@pytest.mark.asyncio
async def test_builder_service_process_dispatches_through_router():
    """``process`` must invoke the compiled router graph with the planned build."""
    service = BuilderService()
    router_graph = SimpleNamespace(
        ainvoke=AsyncMock(return_value={"result_text": "Teardown complete"}),
    )
    service._router_graph = router_graph  # type: ignore[assignment]
    service._initialized = True

    planned = _planned_build()
    result = await service.process("request", {"foo": "bar"}, planned)

    assert result == "Teardown complete"
    router_graph.ainvoke.assert_awaited_once()
    initial_state = router_graph.ainvoke.await_args.args[0]
    assert initial_state["request_text"] == "request"
    assert initial_state["request_context"] == {"foo": "bar"}
    assert initial_state["planned_build"] is planned
