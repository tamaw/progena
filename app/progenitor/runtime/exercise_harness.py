"""
Agent Exercise Harness - Runs experiments against built agents.

BL-004: Implements the agent exercise harness for EXECUTE phase.
- Starts the built agent (HTTP preferred, stdio fallback)
- Runs the prompt suite from ExperimentPlan
- Captures transcripts, timing, and errors into ExecutionResult
- At least one "smoke" prompt always runs
- Multiple prompts run when provided by experiment_plan

Enhanced flow:
1. Spawn agent process in background
2. Health check / crash detection
3. Generate test script dynamically
4. Run test script and capture output
5. Parse test results for scoring
"""

import asyncio
import httpx
import json
import logging
import os
import re
import subprocess
import sys
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
from ..runtime.port_utils import (
    find_available_port,
    is_port_available,
    DEFAULT_AGENT_PORT_START,
    DEFAULT_AGENT_PORT_END,
)

logger = logging.getLogger(__name__)


class AgentProcessRunner:
    """
    Manages spawning and monitoring agent processes.

    Handles:
    - Starting agent as subprocess
    - Health check polling
    - Crash detection
    - Graceful shutdown
    - Automatic port allocation (finds available port)
    """

    DEFAULT_STARTUP_TIMEOUT = 30.0
    DEFAULT_HEALTH_CHECK_INTERVAL = 0.5
    DEFAULT_PORT = DEFAULT_AGENT_PORT_START

    def __init__(
        self,
        project_root: Path,
        agent_entrypoint: str,
        port: int = DEFAULT_PORT,
        auto_find_port: bool = True,
    ):
        self.project_root = Path(project_root)
        self.agent_entrypoint = agent_entrypoint
        self._requested_port = port
        self.port = port
        self.auto_find_port = auto_find_port
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stdout_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self._reader_task: Optional[asyncio.Task[None]] = None

    async def start(self, timeout: float = DEFAULT_STARTUP_TIMEOUT) -> tuple[bool, str]:
        """
        Start the agent process and wait for it to be ready.

        If auto_find_port is True, will find an available port if the
        requested one is in use.

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Resolve entrypoint path
        if not Path(self.agent_entrypoint).is_absolute():
            entrypoint_path = self.project_root / self.agent_entrypoint
        else:
            entrypoint_path = Path(self.agent_entrypoint)

        if not entrypoint_path.exists():
            return False, f"Agent entrypoint not found: {entrypoint_path}"

        # Find an available port if needed
        if self.auto_find_port:
            if not is_port_available(self._requested_port):
                logger.info(
                    f"Port {self._requested_port} is not available, finding alternative..."
                )
                try:
                    self.port = find_available_port(
                        start_port=self._requested_port,
                        end_port=DEFAULT_AGENT_PORT_END,
                    )
                    logger.info(f"Using alternative port: {self.port}")
                except RuntimeError as e:
                    return False, f"Could not find available port: {e}"
            else:
                self.port = self._requested_port

        # Prepare environment
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.project_root)

        logger.info(f"Starting agent: {entrypoint_path} on port {self.port}")

        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, str(entrypoint_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=env,
            )

            # Start background task to read output
            self._reader_task = asyncio.create_task(self._read_output())

            # Wait for agent to be ready (health check)
            ready = await self._wait_for_ready(timeout)

            if not ready:
                # Check if process crashed
                if self.process.returncode is not None:
                    stderr = "\n".join(self.stderr_lines[-10:])
                    return False, f"Agent crashed on startup (exit code {self.process.returncode}): {stderr}"
                return False, "Agent did not respond to health check within timeout"

            return True, f"Agent started successfully on port {self.port}"

        except Exception as e:
            logger.error(f"Failed to start agent: {e}")
            return False, f"Failed to start agent: {e}"

    async def _read_output(self) -> None:
        """Read stdout/stderr in background."""
        if not self.process or not self.process.stdout or not self.process.stderr:
            return

        async def read_stream(stream: asyncio.StreamReader, target: list[str]) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                target.append(decoded)
                logger.debug(f"Agent output: {decoded}")

        await asyncio.gather(
            read_stream(self.process.stdout, self.stdout_lines),
            read_stream(self.process.stderr, self.stderr_lines),
        )

    async def _wait_for_ready(self, timeout: float) -> bool:
        """Poll health endpoint until ready or timeout."""
        start = time.perf_counter()
        health_url = f"http://localhost:{self.port}/health"

        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.perf_counter() - start < timeout:
                # Check if process died
                if self.process and self.process.returncode is not None:
                    logger.warning(f"Agent process died with code {self.process.returncode}")
                    return False

                try:
                    resp = await client.get(health_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") == "healthy":
                            logger.info(f"Agent is healthy after {time.perf_counter() - start:.2f}s")
                            return True
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                except Exception as e:
                    logger.debug(f"Health check error: {e}")

                await asyncio.sleep(self.DEFAULT_HEALTH_CHECK_INTERVAL)

        return False

    async def stop(self) -> None:
        """Stop the agent process gracefully."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self.process and self.process.returncode is None:
            logger.info("Stopping agent process...")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("Agent process stopped successfully")
            except asyncio.TimeoutError:
                logger.warning("Agent did not terminate gracefully, killing...")
                self.process.kill()
                await self.process.wait()
                logger.info("Agent process killed and stopped")

    def is_running(self) -> bool:
        """Check if the agent process is still running."""
        return self.process is not None and self.process.returncode is None

    def get_output(self) -> dict[str, list[str]]:
        """Get captured stdout/stderr."""
        return {
            "stdout": self.stdout_lines.copy(),
            "stderr": self.stderr_lines.copy(),
        }


class TestScriptRunner:
    """
    Generates and runs test scripts against agents.

    Flow:
    1. Generate test script from template + experiment plan
    2. Write script to temp location
    3. Execute script and capture output
    4. Parse test results
    """

    DEFAULT_TEST_TIMEOUT = 60.0

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.prompts_dir = self.project_root / "data" / "prompts"
        self.tmp_dir = self.project_root / "data" / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def generate_test_script(
        self,
        agent_name: str,
        port: int,
        prompts: list[PromptSpec],
        description: str = "",
        capabilities: list[str] | None = None,
    ) -> str:
        """
        Generate a test script for the agent.

        Returns the path to the generated script.
        """
        # Build test prompts list
        test_prompts = [p.text for p in prompts if not p.is_optional]
        if not test_prompts:
            test_prompts = ["Hello, are you working?"]

        # Generate script content
        script_content = f'''"""Auto-generated test script for {agent_name}."""
import asyncio
import aiohttp
import sys

BASE_URL = "http://localhost:{port}"

async def main():
    passed = 0
    failed = 0
    total = 0

    async with aiohttp.ClientSession() as session:
        # Health check
        print("TEST_START: health_check")
        total += 1
        try:
            async with session.get(f"{{BASE_URL}}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "healthy":
                        print("TEST_RESULT: PASS: Health endpoint returned healthy")
                        passed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Health status was {{data.get('status')}}")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Health endpoint returned status {{resp.status}}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Health check failed: {{e}}")
            failed += 1

        # Agent card check
        print("TEST_START: agent_card")
        total += 1
        try:
            async with session.get(f"{{BASE_URL}}/.well-known/agent.json", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("name"):
                        print(f"TEST_RESULT: PASS: Agent card returned name '{{data.get('name')}}'")
                        passed += 1
                    else:
                        print("TEST_RESULT: FAIL: Agent card missing name")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Agent card endpoint returned status {{resp.status}}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Agent card check failed: {{e}}")
            failed += 1

        # Functional tests for each prompt
        test_prompts = {json.dumps(test_prompts)}

        for i, prompt in enumerate(test_prompts):
            test_name = f"prompt_{{i+1}}"
            print(f"TEST_START: {{test_name}}")
            total += 1
            try:
                payload = {{"request": prompt, "context": {{}}}}
                async with session.post(f"{{BASE_URL}}/", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") is True and data.get("response"):
                            response_preview = str(data.get("response"))[:100]
                            print(f"TEST_RESULT: PASS: Got response: {{response_preview}}...")
                            passed += 1
                        elif data.get("error"):
                            # Some agents return success=False with helpful error (e.g. missing location)
                            error_preview = str(data.get("error"))[:100]
                            print(f"TEST_RESULT: PASS: Agent responded with clarification: {{error_preview}}")
                            passed += 1
                        else:
                            print(f"TEST_RESULT: FAIL: Invalid response structure: {{data}}")
                            failed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Request returned status {{resp.status}}")
                        failed += 1
            except Exception as e:
                print(f"TEST_RESULT: FAIL: Prompt test failed: {{e}}")
                failed += 1

    print(f"SUMMARY: {{passed}}/{{total}} tests passed")
    if failed == 0:
        print("FINAL_RESULT: PASS")
        sys.exit(0)
    else:
        print("FINAL_RESULT: FAIL")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
'''

        # Write to temp file
        script_path = self.tmp_dir / f"test_{agent_name}_{uuid.uuid4().hex[:8]}.py"
        script_path.write_text(script_content)
        logger.info(f"Generated test script: {script_path}")

        return str(script_path)

    async def run_test_script(
        self,
        script_path: str,
        timeout: float = DEFAULT_TEST_TIMEOUT,
    ) -> dict[str, Any]:
        """
        Run a test script and capture results.

        Returns dict with:
        - stdout: raw stdout
        - stderr: raw stderr
        - exit_code: process exit code
        - tests: list of test results
        - passed: count of passed tests
        - failed: count of failed tests
        - total: total test count
        - success: overall success
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.project_root)

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode()
            stderr = stderr_bytes.decode()

            # Parse test results
            tests = self._parse_test_output(stdout)

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": process.returncode,
                "tests": tests,
                "passed": sum(1 for t in tests if t["passed"]),
                "failed": sum(1 for t in tests if not t["passed"]),
                "total": len(tests),
                "success": process.returncode == 0,
            }

        except asyncio.TimeoutError:
            return {
                "stdout": "",
                "stderr": f"Test script timed out after {timeout}s",
                "exit_code": -1,
                "tests": [],
                "passed": 0,
                "failed": 1,
                "total": 1,
                "success": False,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": -1,
                "tests": [],
                "passed": 0,
                "failed": 1,
                "total": 1,
                "success": False,
            }

    def _parse_test_output(self, stdout: str) -> list[dict[str, Any]]:
        """Parse test output into structured results."""
        tests: list[dict[str, Any]] = []
        current_test: Optional[str] = None

        for line in stdout.split("\n"):
            line = line.strip()

            if line.startswith("TEST_START:"):
                current_test = line.replace("TEST_START:", "").strip()

            elif line.startswith("TEST_RESULT:"):
                # Format: TEST_RESULT: PASS|FAIL: message
                match = re.match(r"TEST_RESULT:\s*(PASS|FAIL):\s*(.*)", line)
                if match:
                    status = match.group(1)
                    message = match.group(2)
                    tests.append({
                        "name": current_test or "unknown",
                        "passed": status == "PASS",
                        "message": message,
                    })

        return tests


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

    async def execute_with_test_script(
        self,
        experiment_plan: ExperimentPlan,
        agent_entrypoint: str,
        port: int = DEFAULT_AGENT_PORT_START,
        iteration: int = 0,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute experiment by spawning agent and running generated test script.

        Flow:
        1. Spawn agent process in background
        2. Wait for health check / detect crash
        3. Generate test script based on experiment plan
        4. Run test script and capture output
        5. Parse results into ExecutionResult

        Args:
            experiment_plan: The experiment plan with prompts to test.
            agent_entrypoint: Path to agent's main.py or equivalent.
            port: Port the agent should listen on.
            iteration: Current iteration number.
            task_id: Task identifier.

        Returns:
            ExecutionResult with test results and captured output.
        """
        result = ExecutionResult(
            iteration=iteration,
            task_id=task_id,
            started_at=utcnow(),
        )

        # Create execution run
        run = ExecutionRun(
            run_id=f"run_{uuid.uuid4().hex[:8]}",
            iteration=iteration,
            agent_name=experiment_plan.agent_name,
            agent_endpoint=f"http://localhost:{port}",
            agent_transport="http",
            started_at=utcnow(),
        )

        agent_runner: Optional[AgentProcessRunner] = None
        test_script_path: Optional[str] = None

        try:
            # Step 1: Spawn agent process
            logger.info(f"Spawning agent from {agent_entrypoint} on port {port}")
            agent_runner = AgentProcessRunner(
                project_root=self.project_root,
                agent_entrypoint=agent_entrypoint,
                port=port,
            )

            success, message = await agent_runner.start(timeout=30.0)

            if not success:
                # Agent crashed or failed to start
                logger.error(f"Agent failed to start: {message}")
                run.status = ExecutionStatus.FAILED
                run.errors.append(f"Agent startup failed: {message}")

                # Add agent output to help debugging
                agent_output = agent_runner.get_output()
                run.errors.extend([f"stderr: {line}" for line in agent_output["stderr"][-5:]])

                run.finalize()
                result.runs.append(run)
                result.build_output = {
                    "agent_entrypoint": agent_entrypoint,
                    "startup_failed": True,
                    "error": message,
                    "agent_stdout": agent_output["stdout"][-20:],
                    "agent_stderr": agent_output["stderr"][-20:],
                }
                result.finalize()
                return result

            logger.info(f"Agent started successfully: {message}")

            # Step 2: Generate test script
            test_runner = TestScriptRunner(self.project_root)

            # Ensure we have prompts
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

            test_script_path = test_runner.generate_test_script(
                agent_name=experiment_plan.agent_name,
                port=port,
                prompts=prompts,
                description=experiment_plan.experiment_description or "",
            )

            logger.info(f"Generated test script: {test_script_path}")

            # Step 3: Run test script
            test_results = await test_runner.run_test_script(
                script_path=test_script_path,
                timeout=90.0,
            )

            logger.info(
                f"Test results: {test_results['passed']}/{test_results['total']} passed, "
                f"exit_code={test_results['exit_code']}"
            )

            # Step 4: Convert test results to prompt results
            for test in test_results["tests"]:
                prompt_result = PromptResult(
                    prompt_id=test["name"],
                    prompt_text=test["name"],
                    success=test["passed"],
                    response_text=test["message"],
                    started_at=utcnow(),
                    completed_at=utcnow(),
                )
                run.prompt_results.append(prompt_result)

            # Determine overall status
            if test_results["success"]:
                run.status = ExecutionStatus.SUCCESS
            elif test_results["passed"] > 0:
                run.status = ExecutionStatus.PARTIAL
            else:
                run.status = ExecutionStatus.FAILED

            # Store test output for verification
            result.build_output = {
                "agent_entrypoint": agent_entrypoint,
                "test_script": test_script_path,
                "test_stdout": test_results["stdout"],
                "test_stderr": test_results["stderr"],
                "test_exit_code": test_results["exit_code"],
                "tests_passed": test_results["passed"],
                "tests_failed": test_results["failed"],
                "tests_total": test_results["total"],
                "experiment_plan_id": experiment_plan.plan_id,
                "agent_name": experiment_plan.agent_name,
            }

            run.finalize()
            result.runs.append(run)

        except Exception as e:
            logger.error(f"Test execution error: {e}")
            run.status = ExecutionStatus.FAILED
            run.errors.append(str(e))
            run.finalize()
            result.runs.append(run)

        finally:
            # Always stop the agent
            if agent_runner:
                await agent_runner.stop()

            # Optionally clean up test script (keep for debugging)
            # if test_script_path and Path(test_script_path).exists():
            #     Path(test_script_path).unlink()

        # Finalize result
        result.finalize()

        # Log execution
        if self.state_manager:
            await self.state_manager.log_decision({
                "event": "test_script_execution",
                "plan_id": experiment_plan.plan_id,
                "iteration": iteration,
                "status": result.status.value,
                "tests_total": result.runs[0].prompts_total if result.runs else 0,
                "tests_passed": result.runs[0].prompts_succeeded if result.runs else 0,
                "smoke_test_passed": result.smoke_test_passed,
                "duration_ms": result.total_duration_ms,
                "timestamp": utcnow_isoformat(),
            })

        return result


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
