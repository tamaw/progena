"""
Agent Exercise Harness - Starts and tests built agents.

Provides AgentExerciseHarness for running prompt suites against
a newly-built agent, and create_execution_result_from_build for
wrapping build output in a structured ExecutionResult.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..state.execution_schemas import ExecutionResult, ExecutionStatus

if TYPE_CHECKING:
    from ..state.execution_schemas import ExperimentPlan
    from ..state.manager import StateManager

logger = logging.getLogger(__name__)


async def create_execution_result_from_build(
    build_output: dict[str, Any],
    iteration: int,
    current_item: Any,
) -> ExecutionResult:
    """Wrap a BUILD-phase result in an ExecutionResult.

    Used when the execute phase is skipped (work already done in BUILD).
    """
    task_id: str | None = None
    if current_item:
        if hasattr(current_item, "id"):
            task_id = str(current_item.id)
        elif isinstance(current_item, dict):
            task_id = current_item.get("id")

    errors: list[str] = build_output.get("errors", []) if isinstance(build_output, dict) else []
    status = ExecutionStatus.FAILED if errors else ExecutionStatus.SUCCESS

    result = ExecutionResult(
        iteration=iteration,
        task_id=task_id,
        status=status,
        build_output=build_output,
        all_errors=errors,
    )
    return result.finalize()


class AgentExerciseHarness:
    """Exercises a built agent by running a prompt suite against it.

    Spawns the agent process (or connects to an existing endpoint),
    fires prompts from an ExperimentPlan, and records results.
    """

    def __init__(
        self,
        project_root: Path,
        state_manager: StateManager | None = None,
    ) -> None:
        self.project_root = project_root
        self.state_manager = state_manager

    async def execute_with_test_script(
        self,
        experiment_plan: ExperimentPlan,
        agent_entrypoint: str,
        port: int,
        iteration: int,
        task_id: str | None = None,
    ) -> ExecutionResult:
        """Spawn agent, run generated test script, capture results."""
        from ..runtime.time_utils import utcnow

        logger.info(
            "Exercise harness: test script mode entrypoint=%s port=%d",
            agent_entrypoint, port,
        )
        result = ExecutionResult(
            iteration=iteration,
            task_id=task_id,
            status=ExecutionStatus.SKIPPED,
            all_errors=["Exercise harness test-script mode not yet implemented"],
        )
        result.completed_at = utcnow()
        return result

    async def execute_experiment(
        self,
        experiment_plan: ExperimentPlan,
        agent_endpoint: str,
        agent_entrypoint: str | None = None,
        iteration: int = 0,
        task_id: str | None = None,
    ) -> ExecutionResult:
        """Run prompt suite against a running agent endpoint."""
        import httpx

        from ..runtime.time_utils import utcnow

        logger.info("Exercise harness: HTTP mode endpoint=%s", agent_endpoint)
        errors: list[str] = []
        smoke_passed = False

        prompts = experiment_plan.prompts if hasattr(experiment_plan, "prompts") else []
        smoke_prompts = [p for p in prompts if getattr(p, "is_smoke", False)] or prompts[:1]

        for prompt_spec in smoke_prompts:
            prompt_text = getattr(prompt_spec, "prompt", str(prompt_spec))
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{agent_endpoint}/",
                        json={"messages": [{"role": "user", "content": prompt_text}]},
                    )
                    if resp.status_code < 400:
                        smoke_passed = True
                    else:
                        errors.append(f"HTTP {resp.status_code} for smoke prompt")
            except Exception as exc:
                errors.append(f"Smoke prompt error: {exc}")

        status = ExecutionStatus.SUCCESS if smoke_passed else ExecutionStatus.FAILED
        result = ExecutionResult(
            iteration=iteration,
            task_id=task_id,
            status=status,
            all_errors=errors,
            smoke_test_passed=smoke_passed,
        )
        result.completed_at = utcnow()
        return result
