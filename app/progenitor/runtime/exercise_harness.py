"""
Agent Exercise Harness - Runs experiments against built agents.

BL-004: Implements the agent exercise harness for EXECUTE phase.
- Starts the built agent (HTTP preferred, stdio fallback)
- Runs the prompt suite from ExperimentPlan
- Captures transcripts, timing, and errors into ExecutionResult
- At least one "smoke" prompt always runs
- Multiple prompts run when provided by experiment_plan
"""

import asyncio
import httpx
import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..state.execution_schemas import (
    ExecutionResult,
    ExecutionRun,
    ExecutionStatus,
    PromptResult,
    ExperimentPlan,
    PromptSpec,
)
from ..runtime.time_utils import utcnow, utcnow_isoformat

logger = logging.getLogger(__name__)


class AgentExerciseHarness:
    """
    Harness for exercising built agents during EXECUTE phase.

    BL-004: Runs the experiment plan against the agent, capturing
    all results into structured ExecutionResult.
    """

    DEFAULT_HTTP_TIMEOUT = 30.0
    DEFAULT_STDIO_TIMEOUT = 60.0

    def __init__(
        self,
        project_root: Path,
        state_manager=None,
    ):
        """Initialize the exercise harness.

        Args:
            project_root: Root path of the Progenitor project.
            state_manager: Optional state manager for logging.
        """
        self.project_root = Path(project_root)
        self.state_manager = state_manager

    async def execute_experiment(
        self,
        experiment_plan: ExperimentPlan,
        agent_endpoint: Optional[str] = None,
        agent_entrypoint: Optional[str] = None,
        iteration: int = 0,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute an experiment plan against the agent.

        BL-004: Runs the prompt suite and captures results.
        At least one "smoke" prompt always runs.

        Args:
            experiment_plan: The experiment plan to execute.
            agent_endpoint: HTTP endpoint if using HTTP transport.
            agent_entrypoint: Path to agent entrypoint if using stdio.
            iteration: Current iteration number.
            task_id: Task identifier.

        Returns:
            ExecutionResult with all prompt results and timing.
        """
        result = ExecutionResult(
            iteration=iteration,
            task_id=task_id,
            started_at=utcnow(),
        )

        # Determine transport method
        transport = "http" if agent_endpoint else "stdio"

        logger.info(
            f"Starting experiment '{experiment_plan.plan_id}' "
            f"with {len(experiment_plan.prompts)} prompts "
            f"via {transport}"
        )

        # Ensure we have at least a smoke test
        prompts = experiment_plan.prompts
        if not prompts:
            prompts = [
                PromptSpec(
                    id="smoke_default",
                    text="Hello, are you working?",
                    is_smoke_test=True,
                    timeout_ms=10000,
                )
            ]

        # Create execution run
        run = ExecutionRun(
            run_id=f"run_{uuid.uuid4().hex[:8]}",
            iteration=iteration,
            agent_name=experiment_plan.agent_name,
            agent_endpoint=agent_endpoint,
            agent_transport=transport,
            started_at=utcnow(),
        )

        try:
            # Run prompts based on transport
            if transport == "http":
                if not agent_endpoint:
                    raise ValueError("HTTP transport requires agent_endpoint")
                await self._run_http_prompts(run, prompts, agent_endpoint, experiment_plan)
            else:
                await self._run_stdio_prompts(run, prompts, agent_entrypoint, experiment_plan)

            # Finalize run
            run.finalize()
            result.runs.append(run)

        except Exception as e:
            logger.error(f"Experiment execution error: {e}")
            run.status = ExecutionStatus.FAILED
            run.errors.append(str(e))
            run.finalize()
            result.runs.append(run)

        # Store build output reference if available
        result.build_output = {
            "experiment_plan_id": experiment_plan.plan_id,
            "agent_name": experiment_plan.agent_name,
        }

        # Finalize result
        result.finalize()

        # Log execution
        if self.state_manager:
            await self.state_manager.log_decision({
                "event": "experiment_execution",
                "plan_id": experiment_plan.plan_id,
                "iteration": iteration,
                "status": result.status.value,
                "prompts_total": result.runs[0].prompts_total if result.runs else 0,
                "prompts_succeeded": result.runs[0].prompts_succeeded if result.runs else 0,
                "smoke_test_passed": result.smoke_test_passed,
                "duration_ms": result.total_duration_ms,
                "timestamp": utcnow_isoformat(),
            })

        return result

    async def _run_http_prompts(
        self,
        run: ExecutionRun,
        prompts: list[PromptSpec],
        endpoint: str,
        plan: ExperimentPlan,
    ) -> None:
        """Run prompts against an HTTP agent endpoint."""

        consecutive_failures = 0

        async with httpx.AsyncClient(timeout=self.DEFAULT_HTTP_TIMEOUT) as client:
            for prompt_spec in prompts:
                # Check stop-early rules
                if self._should_stop_early(run, plan, consecutive_failures):
                    logger.info("Stopping early based on rules")
                    break

                prompt_result = await self._execute_http_prompt(
                    client, endpoint, prompt_spec
                )
                run.prompt_results.append(prompt_result)

                # Track consecutive failures
                if prompt_result.success:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                # Smoke test failure is fatal
                if prompt_spec.is_smoke_test and not prompt_result.success:
                    logger.warning("Smoke test failed, stopping experiment")
                    run.status = ExecutionStatus.FAILED
                    break

    async def _execute_http_prompt(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        prompt_spec: PromptSpec,
    ) -> PromptResult:
        """Execute a single prompt via HTTP."""

        result = PromptResult(
            prompt_id=prompt_spec.id,
            prompt_text=prompt_spec.text,
            started_at=utcnow(),
        )

        timeout_s = prompt_spec.timeout_ms / 1000.0

        try:
            # Prepare request payload (A2A format)
            payload = {
                "jsonrpc": "2.0",
                "method": "tasks/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"text": prompt_spec.text}],
                    }
                },
                "id": prompt_spec.id,
            }

            start_time = time.perf_counter()

            response = await client.post(
                endpoint,
                json=payload,
                timeout=timeout_s,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result.duration_ms = elapsed_ms
            result.completed_at = utcnow()

            if response.status_code == 200:
                response_data = response.json()
                result.response_data = response_data

                # Extract text from A2A response
                if "result" in response_data:
                    task_result = response_data.get("result", {})
                    artifacts = task_result.get("artifacts", [])
                    if artifacts:
                        parts = artifacts[0].get("parts", [])
                        if parts:
                            result.response_text = parts[0].get("text", "")

                result.success = True

                # Add to transcript
                result.transcript = [
                    {"role": "user", "content": prompt_spec.text},
                    {"role": "assistant", "content": result.response_text},
                ]

                # Validate response expectations
                result.success = self._validate_response(result, prompt_spec)

            else:
                result.success = False
                result.error = f"HTTP {response.status_code}: {response.text[:200]}"
                result.error_type = "http_error"

        except httpx.TimeoutException:
            result.success = False
            result.error = f"Timeout after {timeout_s}s"
            result.error_type = "timeout"
            result.completed_at = utcnow()

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.error_type = "exception"
            result.completed_at = utcnow()

        return result

    async def _run_stdio_prompts(
        self,
        run: ExecutionRun,
        prompts: list[PromptSpec],
        entrypoint: Optional[str],
        plan: ExperimentPlan,
    ) -> None:
        """Run prompts against a stdio agent."""

        if not entrypoint:
            run.status = ExecutionStatus.FAILED
            run.errors.append("No entrypoint provided for stdio agent")
            return

        # Resolve entrypoint path
        if not Path(entrypoint).is_absolute():
            entrypoint_path = self.project_root / entrypoint
        else:
            entrypoint_path = Path(entrypoint)

        if not entrypoint_path.exists():
            run.status = ExecutionStatus.FAILED
            run.errors.append(f"Entrypoint not found: {entrypoint_path}")
            return

        consecutive_failures = 0

        for prompt_spec in prompts:
            # Check stop-early rules
            if self._should_stop_early(run, plan, consecutive_failures):
                logger.info("Stopping early based on rules")
                break

            prompt_result = await self._execute_stdio_prompt(
                entrypoint_path, prompt_spec
            )
            run.prompt_results.append(prompt_result)

            # Track consecutive failures
            if prompt_result.success:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # Smoke test failure is fatal
            if prompt_spec.is_smoke_test and not prompt_result.success:
                logger.warning("Smoke test failed, stopping experiment")
                run.status = ExecutionStatus.FAILED
                break

    async def _execute_stdio_prompt(
        self,
        entrypoint: Path,
        prompt_spec: PromptSpec,
    ) -> PromptResult:
        """Execute a single prompt via stdio."""
        import sys
        import os

        result = PromptResult(
            prompt_id=prompt_spec.id,
            prompt_text=prompt_spec.text,
            started_at=utcnow(),
        )

        timeout_s = prompt_spec.timeout_ms / 1000.0

        try:
            # Prepare environment
            env = os.environ.copy()
            env["PYTHONPATH"] = str(self.project_root)

            # Prepare input
            payload = {
                "request": prompt_spec.text,
                "context": {},
            }

            start_time = time.perf_counter()

            process = await asyncio.create_subprocess_exec(
                sys.executable, str(entrypoint),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=env,
            )

            input_bytes = json.dumps(payload).encode() + b"\n"
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=input_bytes),
                timeout=timeout_s,
            )

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            result.duration_ms = elapsed_ms
            result.completed_at = utcnow()

            if process.returncode == 0:
                stdout_text = stdout.decode().strip()

                # Try to parse JSON response
                try:
                    response_data = json.loads(stdout_text)
                    result.response_data = response_data
                    result.response_text = response_data.get("response", stdout_text)
                except json.JSONDecodeError:
                    result.response_text = stdout_text

                result.success = True

                # Add to transcript
                result.transcript = [
                    {"role": "user", "content": prompt_spec.text},
                    {"role": "assistant", "content": result.response_text},
                ]

                # Validate response expectations
                result.success = self._validate_response(result, prompt_spec)

            else:
                result.success = False
                result.error = f"Exit code {process.returncode}: {stderr.decode()[:200]}"
                result.error_type = "exit_error"

        except asyncio.TimeoutError:
            result.success = False
            result.error = f"Timeout after {timeout_s}s"
            result.error_type = "timeout"
            result.completed_at = utcnow()

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.error_type = "exception"
            result.completed_at = utcnow()

        return result

    def _validate_response(
        self,
        result: PromptResult,
        spec: PromptSpec,
    ) -> bool:
        """Validate response against prompt spec expectations."""

        if not result.response_text:
            return spec.is_optional or not spec.expected_response_contains

        response_lower = result.response_text.lower()

        # Check required substrings
        for expected in spec.expected_response_contains:
            if expected.lower() not in response_lower:
                return False

        # Check forbidden substrings
        for forbidden in spec.expected_response_not_contains:
            if forbidden.lower() in response_lower:
                return False

        return True

    def _should_stop_early(
        self,
        run: ExecutionRun,
        plan: ExperimentPlan,
        consecutive_failures: int,
    ) -> bool:
        """Check if experiment should stop early based on rules."""

        for rule in plan.stop_early_rules:
            if rule.rule_type == "consecutive_failures":
                if consecutive_failures >= rule.threshold:
                    return True

            elif rule.rule_type == "score_threshold":
                # Calculate current score
                if run.prompt_results:
                    current_score = run.prompts_succeeded / len(run.prompt_results)
                    if current_score >= rule.threshold:
                        return True

        return False


async def create_execution_result_from_build(
    build_output: dict[str, Any],
    iteration: int,
    work_item: Optional[Any] = None,
) -> ExecutionResult:
    """
    Create an ExecutionResult from BUILD phase output.

    BL-001: Every iteration produces ExecutionResult payload,
    even on failures (with errors populated).

    This is used when EXECUTE runs without an experiment plan
    (e.g., direct answer or non-agent work items).
    """
    result = ExecutionResult(
        iteration=iteration,
        started_at=utcnow(),
        build_output=build_output,
    )

    # Check for errors in build output
    errors = []
    if isinstance(build_output, dict):
        if build_output.get("error"):
            errors.append(str(build_output["error"]))
        if build_output.get("errors"):
            errors.extend(build_output["errors"])

    if errors:
        result.status = ExecutionStatus.FAILED
        result.all_errors = errors
    else:
        result.status = ExecutionStatus.SUCCESS

    result.finalize()
    return result
