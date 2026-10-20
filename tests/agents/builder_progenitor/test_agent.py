"""Tests for Builder agent execution safeguards."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.builder_progenitor.agent import BuilderService
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
