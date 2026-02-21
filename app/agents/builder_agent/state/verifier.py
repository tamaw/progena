"""
Verifier - Scores outputs against ideal state criteria.

BL-006: VERIFY consumes structured execution+validation data to compute
as many criterion scores as possible mechanically, then optionally uses
the LLM for subjective criteria.

Verification report cites concrete evidence from execution runs.

Implements the VERIFY phase of the inner loop per loop.spec.md.
Every iteration produces:
- Score per criterion
- Pass/fail summary
- Evidence or heuristic notes
- Gaps and next actions
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from ..llm.prompts import PromptTemplates
from ..runtime.time_utils import utcnow
from .execution_schemas import (
    CriterionEvidence,
    ExecutionResult,
    ValidationResultV2,
    VerificationReportV2,
)
from .ideal_state import AntiCriterion, IdealState, SuccessCriterion

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


class CriterionScore(BaseModel):
    """Score for a single criterion."""

    criterion_id: str
    score: float = Field(ge=0.0, le=1.0, description="Score 0-1")
    passed: bool = Field(description="Did it meet target?")
    evidence: str = Field(description="Evidence for this score")
    notes: str = Field(default="", description="Additional notes")


class AntiCriterionViolation(BaseModel):
    """Violation of an anti-criterion."""

    anti_criterion_id: str
    severity: float = Field(description="Severity of violation")
    description: str = Field(description="What was violated")
    penalty: float = Field(description="Score penalty applied")


class VerificationReport(BaseModel):
    """Complete verification report for an iteration."""

    iteration: int = Field(description="Iteration number")
    timestamp: str = Field(description="When verification ran")

    # Scores
    criterion_scores: list[CriterionScore] = Field(default_factory=list)
    anti_criterion_violations: list[AntiCriterionViolation] = Field(default_factory=list)

    overall_score: float = Field(ge=0.0, le=1.0, description="Weighted overall score")
    passed: bool = Field(description="Did it pass minimum threshold?")

    # Analysis
    gaps: list[str] = Field(default_factory=list, description="What's still missing")
    next_actions: list[str] = Field(default_factory=list, description="Suggested next steps")

    # Summary
    summary: str = Field(description="Human-readable summary")

    def to_compact_summary(self) -> str:
        """Generate compact verification summary for final output."""
        passed_count = sum(1 for s in self.criterion_scores if s.passed)
        total_count = len(self.criterion_scores)

        lines = [
            f"Verification Score: {self.overall_score:.2f} ({'PASS' if self.passed else 'FAIL'})",
            f"Criteria: {passed_count}/{total_count} passed",
        ]

        if self.anti_criterion_violations:
            lines.append(f"Violations: {len(self.anti_criterion_violations)}")

        if self.gaps:
            lines.append(f"Gaps: {', '.join(self.gaps[:3])}")

        return "\n".join(lines)


class Verifier:
    """Verifies outputs against ideal state criteria.

    BL-006: Enhanced to consume structured execution+validation data
    and compute mechanical scores before falling back to LLM.

    Can operate in two modes:
    - LLM-assisted: Uses LLM for nuanced scoring when llm_client is provided
    - Heuristic-only: Falls back to rule-based scoring when no LLM available

    The heuristic mode is useful for offline testing, cost reduction, or when
    LLM is temporarily unavailable.
    """

    def __init__(self, llm_client=None, heuristic_only: bool = False):
        """Initialize the verifier.

        Args:
            llm_client: Optional LLM client for nuanced verification.
            heuristic_only: If True, never use LLM even if available.
        """
        self.llm_client = llm_client
        self.heuristic_only = heuristic_only

        if self.heuristic_only:
            logger.info("Verifier initialized in heuristic-only mode")
        elif self.llm_client is None:
            logger.info("Verifier initialized without LLM client - using heuristics")
        else:
            logger.debug("Verifier initialized with LLM client")

    @property
    def uses_llm(self) -> bool:
        """Check if verifier will use LLM for scoring."""
        return self.llm_client is not None and not self.heuristic_only

    def _ensure_ideal_state(self, ideal_state: Any) -> IdealState:
        """Convert ideal_state dict to IdealState object if needed."""
        if isinstance(ideal_state, IdealState):
            return ideal_state
        if isinstance(ideal_state, dict):
            return IdealState(**ideal_state)
        raise TypeError(f"Expected IdealState or dict, got {type(ideal_state)}")

    async def verify_with_structured_data(
        self,
        ideal_state: IdealState | dict,
        execution_result: ExecutionResult | dict | None,
        validation_result: ValidationResultV2 | dict | None,
        iteration: int,
    ) -> VerificationReportV2:
        """
        BL-006: Verify using structured execution+validation data.

        Computes as many criterion scores as possible mechanically,
        then optionally uses LLM for subjective criteria.

        Args:
            ideal_state: The target state with criteria
            execution_result: Structured ExecutionResult from EXECUTE
            validation_result: Structured ValidationResultV2 from VALIDATE
            iteration: Current iteration number

        Returns:
            VerificationReportV2 with evidence citations from execution runs
        """

        ideal_state = self._ensure_ideal_state(ideal_state)

        # Parse execution result if dict
        exec_data = None
        if execution_result:
            if isinstance(execution_result, dict):
                exec_data = execution_result
            else:
                exec_data = execution_result.model_dump()

        # Parse validation result if dict
        valid_data = None
        if validation_result:
            if isinstance(validation_result, dict):
                valid_data = validation_result
            else:
                valid_data = validation_result.model_dump()

        criterion_evidences: list[CriterionEvidence] = []
        evidence_citations: list[str] = []
        mechanical_count = 0
        llm_count = 0

        # Score each criterion using mechanical checks first
        for criterion in ideal_state.success_criteria:
            evidence = await self._score_criterion_with_data(
                criterion=criterion,
                exec_data=exec_data,
                valid_data=valid_data,
            )
            criterion_evidences.append(evidence)

            # Track scoring method
            if evidence.scoring_method in ("heuristic", "mechanical"):
                mechanical_count += 1
            else:
                llm_count += 1

            # Collect evidence citations
            if evidence.execution_evidence:
                evidence_citations.append(
                    f"[{criterion.id}] Execution: {evidence.execution_evidence[:100]}"
                )
            if evidence.validation_evidence:
                evidence_citations.append(
                    f"[{criterion.id}] Validation: {evidence.validation_evidence[:100]}"
                )

        # Check anti-criteria violations
        violations = []
        for anti_criterion in ideal_state.anti_criteria:
            violation = self._check_anti_criterion_with_data(
                anti_criterion, exec_data, valid_data
            )
            if violation:
                violations.append(violation)

        # Calculate overall score
        if criterion_evidences:
            weighted_sum = sum(e.score * ideal_state.success_criteria[i].weight
                              for i, e in enumerate(criterion_evidences))
            total_weight = sum(c.weight for c in ideal_state.success_criteria)
            overall_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        else:
            overall_score = 0.5  # Default when no criteria

        # Apply violation penalties
        for violation in violations:
            penalty = violation.get("penalty", 0.1)
            overall_score = max(0.0, overall_score - penalty)

        # Determine pass/fail
        min_score = self._get_minimum_score(ideal_state)
        passed = overall_score >= min_score

        # Identify gaps with DETAILED execution evidence (not just criterion name)
        gaps = []
        for i, e in enumerate(criterion_evidences):
            if not e.passed:
                criterion = ideal_state.success_criteria[i]
                gap_detail = f"{criterion.id}: Target: {criterion.target_score:.2f}, Achieved: {e.score:.2f}"

                # Include execution evidence details if available
                if e.execution_evidence:
                    gap_detail += f" | Execution: {e.execution_evidence}"
                if e.validation_evidence:
                    gap_detail += f" | Validation: {e.validation_evidence}"
                if e.llm_assessment:
                    gap_detail += f" | Assessment: {e.llm_assessment[:200]}"

                gaps.append(gap_detail)

        # Enrich gaps with raw execution data summary if available
        if exec_data and not passed:
            status = exec_data.get("status", "unknown")
            errors = exec_data.get("all_errors", [])
            runs = exec_data.get("runs", [])

            # Add execution summary to gaps
            exec_summary_parts = [f"Execution status: {status}"]
            if errors:
                exec_summary_parts.append(f"Errors: {'; '.join(str(e)[:100] for e in errors[:3])}")
            if runs:
                run_summaries = []
                for run in runs[:2]:  # Show first 2 runs
                    prompts_passed = run.get("prompts_passed", 0)
                    prompts_total = run.get("prompts_total", 0)
                    run_summaries.append(f"prompts {prompts_passed}/{prompts_total}")
                exec_summary_parts.append(f"Runs: {', '.join(run_summaries)}")

            gaps.append(f"[Execution Summary] {' | '.join(exec_summary_parts)}")

            # Include build_output details for debugging (agent stderr/stdout on failure)
            build_output = exec_data.get("build_output", {})
            if build_output:
                build_parts = []
                if build_output.get("startup_failed"):
                    build_parts.append("STARTUP FAILED")
                    if build_output.get("error"):
                        build_parts.append(f"Error: {str(build_output['error'])[:500]}")
                    agent_stderr = build_output.get("agent_stderr", [])
                    if agent_stderr:
                        # Include up to last 100 lines of stderr for debugging
                        stderr_lines = [str(line) for line in agent_stderr[-100:]]
                        stderr_snippet = "\n".join(stderr_lines)
                        build_parts.append(f"Stderr ({len(agent_stderr)} lines, last {min(len(agent_stderr), 100)}):\n{stderr_snippet}")
                    agent_stdout = build_output.get("agent_stdout", [])
                    if agent_stdout:
                        # Include up to last 50 lines of stdout
                        stdout_lines = [str(line) for line in agent_stdout[-50:]]
                        stdout_snippet = "\n".join(stdout_lines)
                        build_parts.append(f"Stdout ({len(agent_stdout)} lines, last {min(len(agent_stdout), 50)}):\n{stdout_snippet}")
                elif build_output.get("test_stderr"):
                    # Include test stderr if present (up to 100 lines)
                    test_stderr = build_output.get("test_stderr", "")
                    if test_stderr.strip():
                        stderr_lines = test_stderr.strip().split("\n")[-100:]
                        build_parts.append(f"Test stderr ({len(stderr_lines)} lines):\n{chr(10).join(stderr_lines)}")

                if build_parts:
                    gaps.append(f"[Build Output] {' | '.join(build_parts)}")

        # Suggest next actions
        next_actions = self._suggest_next_actions_from_evidence(criterion_evidences, gaps)

        # Generate summary with evidence citations
        passed_count = sum(1 for e in criterion_evidences if e.passed)
        total_count = len(criterion_evidences)
        summary = (
            f"Verification: {overall_score:.2f} ({'PASS' if passed else 'FAIL'}). "
            f"Criteria: {passed_count}/{total_count} passed. "
            f"Scoring: {mechanical_count} mechanical, {llm_count} LLM. "
            f"Violations: {len(violations)}."
        )

        report = VerificationReportV2(
            iteration=iteration,
            verified_at=utcnow(),
            execution_result_ref=exec_data.get("task_id") if exec_data else None,
            validation_result_ref=valid_data.get("execution_result_ref") if valid_data else None,
            criterion_scores=criterion_evidences,
            anti_criterion_violations=violations,
            overall_score=overall_score,
            passed=passed,
            mechanical_score_count=mechanical_count,
            llm_score_count=llm_count,
            gaps=gaps,
            next_actions=next_actions,
            summary=summary,
            evidence_citations=evidence_citations,
        )

        return report.compute_score_breakdown()

    async def _score_criterion_with_data(
        self,
        criterion: SuccessCriterion,
        exec_data: dict | None,
        valid_data: dict | None,
    ) -> CriterionEvidence:
        """
        BL-006: Score a criterion using execution+validation data mechanically.
        Falls back to LLM only for subjective criteria.
        """
        evidence = CriterionEvidence(
            criterion_id=criterion.id,
            score=0.0,
            passed=False,
            scoring_method="heuristic",
        )

        # Try mechanical scoring first based on criterion type
        criterion_type = criterion.criterion_type.value if hasattr(criterion.criterion_type, "value") else str(criterion.criterion_type)

        if criterion_type == "functional":
            # Functional criteria can often be scored mechanically
            score, exec_ev, valid_ev = self._mechanical_functional_score(exec_data, valid_data, criterion)
            evidence.score = score
            evidence.execution_evidence = exec_ev
            evidence.validation_evidence = valid_ev
            evidence.scoring_method = "mechanical"

        elif criterion_type == "performance":
            # Performance can be scored from timing data
            score, exec_ev = self._mechanical_performance_score(exec_data, criterion)
            evidence.score = score
            evidence.execution_evidence = exec_ev
            evidence.scoring_method = "mechanical"

        elif criterion_type in ("quality", "user_experience", "maintainability"):
            # Subjective criteria may need LLM
            if self.uses_llm:
                score, llm_assessment, reasoning_trace = await self._llm_score_criterion(
                    criterion, exec_data, valid_data
                )
                evidence.score = score
                evidence.llm_assessment = llm_assessment
                evidence.reasoning_trace = reasoning_trace
                evidence.scoring_method = "llm_judge"
            else:
                # Fallback heuristic for subjective criteria
                evidence.score = self._heuristic_subjective_score(exec_data, valid_data)
                evidence.scoring_method = "heuristic"

        else:
            # Default heuristic
            evidence.score = self._heuristic_subjective_score(exec_data, valid_data)

        evidence.passed = evidence.score >= criterion.target_score
        return evidence

    def _mechanical_functional_score(
        self,
        exec_data: dict | None,
        valid_data: dict | None,
        _criterion: SuccessCriterion,
    ) -> tuple[float, str, str]:
        """
        BL-006: Mechanically score functional criteria using structured data.

        Scoring breakdown:
        - Smoke test passed: +0.35
        - All prompts passed: +0.35
        - Execution status success/partial: +0.15/+0.10
        - No execution errors: +0.15
        - Runs actually executed: bonus for coverage
        - Skipped/no data: penalized appropriately
        - Test script results (when available): factored into scoring
        - BUILD FAILURE: 0.0 score (agent didn't even start!)
        """
        exec_evidence = ""
        valid_evidence = ""
        score = 0.0

        if not exec_data:
            # No execution data at all - can't verify
            return 0.1, "No execution data available.", ""

        status = exec_data.get("status", "unknown")
        runs = exec_data.get("runs", [])
        errors = exec_data.get("all_errors", [])
        build_output = exec_data.get("build_output", {})

        # CRITICAL: Check for build/startup failure FIRST
        # If the agent failed to start, score is 0.0 - nothing else matters
        if build_output and build_output.get("startup_failed"):
            startup_error = build_output.get("error", "Unknown startup error")
            agent_stderr = build_output.get("agent_stderr", [])

            exec_evidence = f"BUILD FAILURE - Agent failed to start: {startup_error}. "
            if agent_stderr:
                # Extract the most relevant error (usually syntax errors)
                stderr_snippet = "; ".join(agent_stderr[-3:])[:300]
                exec_evidence += f"Stderr: {stderr_snippet}"

            return 0.0, exec_evidence.strip(), "Agent never started - cannot evaluate output."

        # Check for test script results (from execute_with_test_script)
        tests_passed = build_output.get("tests_passed") if build_output else None
        tests_total = build_output.get("tests_total") if build_output else None
        test_stdout = build_output.get("test_stdout", "") if build_output else ""

        # Handle skipped execution - this is a problem!
        if status == "skipped":
            exec_evidence = "Execution was skipped (no prompts ran). "
            # Give minimal score - we can't really verify without running
            if not errors:
                exec_evidence += "No errors in build output. "
                score = 0.15  # Slightly better than nothing
            else:
                exec_evidence += f"{len(errors)} errors in build. "
                score = 0.05
            return score, exec_evidence.strip(), ""

        # If we have test script results, use those for more accurate scoring
        if tests_total is not None and tests_total > 0:
            test_pass_rate = tests_passed / tests_total if tests_passed else 0
            exec_evidence += f"Test script: {tests_passed}/{tests_total} tests passed ({test_pass_rate:.0%}). "

            # Score based on test results - this is primary evidence
            score += 0.7 * test_pass_rate  # Up to 0.7 from tests

            # Check for actual responses in stdout
            if "TEST_RESULT: PASS:" in test_stdout:
                exec_evidence += "Got successful responses from agent. "
                score += 0.15

            # Check for failures
            if "TEST_RESULT: FAIL:" in test_stdout:
                # Count failures in output
                fail_count = test_stdout.count("TEST_RESULT: FAIL:")
                exec_evidence += f"Had {fail_count} test failures. "
                # Already penalized by test_pass_rate, so just note it

            # No errors bonus
            if not errors:
                score += 0.15
                exec_evidence += "No errors. "
            else:
                exec_evidence += f"{len(errors)} errors found. "

            return min(max(score, 0.0), 1.0), exec_evidence.strip(), valid_evidence.strip()

        # Fallback to original scoring logic if no test script results
        # Check smoke test passed
        if exec_data.get("smoke_test_passed"):
            score += 0.35
            exec_evidence += "Smoke test passed. "
        elif runs:
            # Smoke test failed but we have runs
            exec_evidence += "Smoke test failed. "

        # Check all prompts passed
        if exec_data.get("all_prompts_passed"):
            score += 0.35
            exec_evidence += "All prompts passed. "
        elif runs:
            # Calculate partial credit based on prompt pass rate
            total_prompts = sum(r.get("prompts_total", 0) for r in runs)
            passed_prompts = sum(r.get("prompts_passed", 0) for r in runs)
            if total_prompts > 0:
                pass_rate = passed_prompts / total_prompts
                partial_score = 0.35 * pass_rate
                score += partial_score
                exec_evidence += f"Prompts: {passed_prompts}/{total_prompts} passed ({pass_rate:.0%}). "

        # Execution status bonus
        if status == "success":
            score += 0.15
            exec_evidence += "Execution succeeded. "
        elif status == "partial":
            score += 0.10
            exec_evidence += "Execution partially succeeded. "
        elif status == "failed":
            exec_evidence += "Execution failed. "
        elif status == "timeout":
            exec_evidence += "Execution timed out. "

        # Check for errors
        if not errors:
            score += 0.15
            exec_evidence += "No errors. "
        else:
            # Penalize based on error count
            error_penalty = min(len(errors) * 0.05, 0.15)
            score -= error_penalty
            exec_evidence += f"{len(errors)} errors found. "

        # Bonus for actual coverage (runs executed)
        if runs:
            run_count = len(runs)
            if run_count >= 3:
                score += 0.05
                exec_evidence += f"Good coverage ({run_count} runs). "
            elif run_count == 0:
                exec_evidence += "No test runs executed. "

        # Validation data
        if valid_data:
            error_count = valid_data.get("error_count", 0)
            warning_count = valid_data.get("warning_count", 0)
            if error_count == 0:
                valid_evidence += "No validation errors. "
                score += 0.05
            else:
                valid_evidence += f"{error_count} validation errors. "
                score -= 0.1 * min(error_count, 3)
            if warning_count > 0:
                valid_evidence += f"{warning_count} warnings. "

        return min(max(score, 0.0), 1.0), exec_evidence.strip(), valid_evidence.strip()

    def _mechanical_performance_score(
        self,
        exec_data: dict | None,
        _criterion: SuccessCriterion,
    ) -> tuple[float, str]:
        """
        BL-006: Mechanically score performance criteria from timing data.
        """
        if not exec_data:
            return 0.5, "No execution data available"

        evidence = ""
        score = 0.5  # Base score

        # Check total duration
        total_duration = exec_data.get("total_duration_ms")
        if total_duration:
            evidence += f"Total duration: {total_duration:.0f}ms. "
            if total_duration < 5000:  # < 5 seconds
                score = 1.0
            elif total_duration < 30000:  # < 30 seconds
                score = 0.8
            elif total_duration < 60000:  # < 1 minute
                score = 0.6
            else:
                score = 0.4

        # Check average response time from runs
        runs = exec_data.get("runs", [])
        if runs:
            avg_times = [r.get("avg_response_time_ms") for r in runs if r.get("avg_response_time_ms")]
            if avg_times:
                avg_response = sum(avg_times) / len(avg_times)
                evidence += f"Avg response time: {avg_response:.0f}ms. "
                if avg_response < 1000:
                    score = min(score + 0.2, 1.0)
                elif avg_response > 10000:
                    score = max(score - 0.2, 0.0)

        return score, evidence.strip()

    def _heuristic_subjective_score(
        self,
        exec_data: dict | None,
        valid_data: dict | None,
    ) -> float:
        """Heuristic fallback for subjective criteria.

        Provides more nuanced scoring based on available evidence.
        """
        score = 0.3  # Lower base score since this is a fallback

        if not exec_data:
            return 0.2  # Can't evaluate without data

        status = exec_data.get("status", "unknown")

        # Handle skipped case
        if status == "skipped":
            return 0.15  # Very low - we didn't actually test

        # Smoke test is a strong signal
        if exec_data.get("smoke_test_passed"):
            score += 0.25
        elif exec_data.get("smoke_test_passed") is False:
            score -= 0.1  # Explicit failure is bad

        # Execution status
        if status == "success":
            score += 0.25
        elif status == "partial":
            score += 0.15
        elif status == "failed":
            score -= 0.1

        # Prompt pass rate (if available)
        runs = exec_data.get("runs", [])
        if runs:
            total_prompts = sum(r.get("prompts_total", 0) for r in runs)
            passed_prompts = sum(r.get("prompts_passed", 0) for r in runs)
            if total_prompts > 0:
                pass_rate = passed_prompts / total_prompts
                score += 0.2 * pass_rate

        # Validation
        if valid_data:
            if valid_data.get("error_count", 0) == 0:
                score += 0.1
            else:
                score -= 0.05 * min(valid_data.get("error_count", 0), 2)

        return max(min(score, 1.0), 0.0)

    async def _llm_score_criterion(
        self,
        criterion: SuccessCriterion,
        exec_data: dict | None,
        valid_data: dict | None,
    ) -> tuple[float, str, str | None]:
        """Use LLM to score subjective criterion.

        Enhanced to include test script output and build failure context for better evaluation.
        """
        if not self.llm_client:
            return 0.5, "LLM not available", None

        # Build context from data
        context_parts = [f"Criterion: {criterion.description}"]

        if exec_data:
            context_parts.append(f"Execution status: {exec_data.get('status', 'unknown')}")
            context_parts.append(f"Smoke test passed: {exec_data.get('smoke_test_passed', 'unknown')}")
            errors = exec_data.get('all_errors', [])
            if errors:
                context_parts.append(f"Errors: {'; '.join(str(e)[:200] for e in errors[:5])}")

            # CRITICAL: Check for build/startup failures first - these mean the agent never ran
            build_output = exec_data.get('build_output', {})
            if build_output:
                startup_failed = build_output.get('startup_failed', False)
                if startup_failed:
                    context_parts.append("\n⚠️ CRITICAL BUILD FAILURE - Agent did not start!")
                    context_parts.append(f"Startup Error: {build_output.get('error', 'Unknown error')}")
                    # Include stderr for debugging context
                    agent_stderr = build_output.get('agent_stderr', [])
                    if agent_stderr:
                        stderr_text = '\n'.join(agent_stderr[-10:])  # Last 10 lines
                        context_parts.append(f"Agent stderr (last 10 lines):\n{stderr_text}")
                    agent_stdout = build_output.get('agent_stdout', [])
                    if agent_stdout:
                        stdout_text = '\n'.join(agent_stdout[-5:])
                        context_parts.append(f"Agent stdout (last 5 lines):\n{stdout_text}")
                else:
                    # Agent started - check for test output
                    test_stdout = build_output.get('test_stdout', '')
                    if test_stdout:
                        # Include relevant portion of test output
                        context_parts.append(f"Test Output:\n{test_stdout[:1500]}")

                    tests_passed = build_output.get('tests_passed')
                    tests_total = build_output.get('tests_total')
                    if tests_passed is not None and tests_total is not None:
                        context_parts.append(f"Test Results: {tests_passed}/{tests_total} tests passed")

        if valid_data:
            findings = valid_data.get('findings', [])
            if findings:
                finding_msgs = [f.get('message', '') for f in findings[:3]]
                context_parts.append(f"Validation findings: {'; '.join(finding_msgs)}")

        prompt = _prompts.get(
            "verifier.criterion_scoring.md",
            context_parts=chr(10).join(context_parts),
        )

        try:
            reasoning_trace = None
            if hasattr(self.llm_client, "generate_with_reasoning"):
                response, reasoning_trace = await self.llm_client.generate_with_reasoning(
                    prompt=prompt,
                    system_prompt=_prompts.get("builder_agent/verifier_criterion_scoring_system.md"),
                )
            else:
                response = await self.llm_client.generate(
                    prompt=prompt,
                    system_prompt=_prompts.get("builder_agent/verifier_criterion_scoring_system.md"),
                )

            # Parse response
            if "SCORE:" in response:
                parts = response.split("|")
                score_part = parts[0].replace("SCORE:", "").strip()
                try:
                    score = float(score_part)
                    score = max(0.0, min(1.0, score))
                except ValueError:
                    score = 0.5
                explanation = parts[1].replace("EXPLANATION:", "").strip() if len(parts) > 1 else response
            else:
                score = 0.5
                explanation = response[:200]

            return score, explanation, reasoning_trace

        except Exception as e:
            logger.warning(f"LLM scoring failed: {e}")
            return 0.5, f"LLM scoring error: {e}", None

    def _check_anti_criterion_with_data(
        self,
        anti_criterion: AntiCriterion,
        exec_data: dict | None,
        _valid_data: dict | None,
    ) -> dict | None:
        """Check if anti-criterion is violated using structured data."""

        description_lower = anti_criterion.description.lower()

        # Check for common anti-criteria patterns
        if ("error" in description_lower or "fail" in description_lower) and exec_data and exec_data.get("all_errors"):
            return {
                "anti_criterion_id": anti_criterion.id,
                "severity": anti_criterion.severity,
                "description": f"Errors present: {len(exec_data['all_errors'])} errors",
                "penalty": 0.1 * anti_criterion.severity,
            }

        if "timeout" in description_lower and exec_data:
            duration = exec_data.get("total_duration_ms", 0)
            if duration > 120000:  # > 2 minutes
                return {
                    "anti_criterion_id": anti_criterion.id,
                    "severity": anti_criterion.severity,
                    "description": f"Execution exceeded timeout threshold: {duration}ms",
                    "penalty": 0.15 * anti_criterion.severity,
                }

        if ("crash" in description_lower or "exception" in description_lower) and exec_data:
            for error in exec_data.get("all_errors", []):
                if "exception" in error.lower() or "crash" in error.lower():
                    return {
                        "anti_criterion_id": anti_criterion.id,
                        "severity": anti_criterion.severity,
                        "description": f"Exception/crash detected: {error[:100]}",
                        "penalty": 0.2 * anti_criterion.severity,
                    }

        return None

    def _suggest_next_actions_from_evidence(
        self,
        criterion_evidences: list[CriterionEvidence],
        _gaps: list[str],
    ) -> list[str]:
        """Generate next actions from evidence."""
        actions = []

        # Find lowest scoring criteria
        sorted_evidences = sorted(criterion_evidences, key=lambda e: e.score)

        for evidence in sorted_evidences[:3]:
            if not evidence.passed:
                if evidence.execution_evidence and "error" in evidence.execution_evidence.lower():
                    actions.append(f"Fix errors for criterion {evidence.criterion_id}")
                elif evidence.scoring_method == "llm_judge":
                    actions.append(f"Review subjective criterion {evidence.criterion_id}")
                else:
                    actions.append(f"Improve criterion {evidence.criterion_id} (score: {evidence.score:.2f})")

        return actions[:5]

    async def verify(
        self,
        ideal_state: IdealState | dict,
        output: Any,
        iteration: int,
        context: dict[str, Any] | None = None,
    ) -> VerificationReport:
        """
        Verify output against ideal state.

        Args:
            ideal_state: The target state with criteria (IdealState or dict)
            output: The candidate output to verify
            iteration: Current iteration number
            context: Additional context (e.g., tool results, logs)

        Returns:
            VerificationReport with scores and analysis
        """
        from ..runtime.time_utils import utcnow_isoformat

        # Ensure we have an IdealState object (may come as dict from LangGraph state)
        ideal_state = self._ensure_ideal_state(ideal_state)

        context = context or {}
        criterion_scores = []
        violations = []

        try:
            # Score each success criterion
            for criterion in ideal_state.success_criteria:
                score_result = await self._score_criterion(
                    criterion, output, context
                )
                criterion_scores.append(score_result)

            # Check anti-criteria
            for anti_criterion in ideal_state.anti_criteria:
                violation = await self._check_anti_criterion(
                    anti_criterion, output, context
                )
                if violation:
                    violations.append(violation)

            # Calculate overall score
            overall_score = self._calculate_overall_score(
                criterion_scores, violations, ideal_state
            )

            # Determine if passed
            min_score = self._get_minimum_score(ideal_state)
            passed = overall_score >= min_score

            # Analyze gaps
            gaps = self._identify_gaps(criterion_scores)
            next_actions = self._suggest_next_actions(criterion_scores, gaps)

            # Generate summary
            summary = self._generate_summary(
                overall_score, passed, criterion_scores, violations, gaps
            )

            report = VerificationReport(
                iteration=iteration,
                timestamp=utcnow_isoformat(),
                criterion_scores=criterion_scores,
                anti_criterion_violations=violations,
                overall_score=overall_score,
                passed=passed,
                gaps=gaps,
                next_actions=next_actions,
                summary=summary,
            )

            return report

        except Exception:
            raise

    async def _score_criterion(
        self,
        criterion: SuccessCriterion,
        output: Any,
        context: dict,
    ) -> CriterionScore:
        """Score a single criterion."""

        # Use appropriate verification method
        if criterion.measurement.lower().startswith("test"):
            score, evidence = await self._test_based_verification(
                criterion, output, context
            )
        elif criterion.measurement.lower().startswith("heuristic"):
            score, evidence = self._heuristic_verification(
                criterion, output, context
            )
        elif self.uses_llm:
            score, evidence = await self._llm_based_verification(
                criterion, output, context
            )
        else:
            # Fallback to basic heuristic when no LLM
            score, evidence = self._heuristic_verification(
                criterion, output, context
            )

        passed = score >= criterion.target_score

        return CriterionScore(
            criterion_id=criterion.id,
            score=score,
            passed=passed,
            evidence=evidence,
            notes=f"Target: {criterion.target_score:.2f}, Achieved: {score:.2f}",
        )

    async def _test_based_verification(
        self, _criterion: SuccessCriterion, _output: Any, context: dict
    ) -> tuple[float, str]:
        """Run test-based verification."""
        # Check if tests were run and passed
        validation_result = context.get("validation_result", {})

        if validation_result.get("success"):
            return 1.0, "All tests passed"
        elif "pytest" in validation_result.get("checks_run", []):
            return 0.5, "Tests ran but some failed"
        else:
            return 0.3, "Tests not run or unavailable"

    def _heuristic_verification(
        self, _criterion: SuccessCriterion, output: Any, context: dict
    ) -> tuple[float, str]:
        """Heuristic-based verification."""
        score = 0.0
        evidence_parts = []

        # Heuristic 1: Output exists and is non-empty
        if output:
            score += 0.3
            evidence_parts.append("Output exists")

        # Heuristic 2: Output has reasonable length
        output_str = str(output)
        if 10 < len(output_str) < 10000:
            score += 0.2
            evidence_parts.append("Reasonable length")

        # Heuristic 3: Check for errors in context
        if not context.get("errors"):
            score += 0.3
            evidence_parts.append("No errors")

        # Heuristic 4: Check if task was marked complete
        if context.get("completed"):
            score += 0.2
            evidence_parts.append("Marked complete")

        evidence = "; ".join(evidence_parts) if evidence_parts else "Minimal evidence"
        return min(score, 1.0), evidence

    async def _llm_based_verification(
        self, criterion: SuccessCriterion, output: Any, context: dict
    ) -> tuple[float, str]:
        """Use LLM to judge quality."""
        prompt = _prompts.get(
            "verifier.llm_verification.md",
            criterion_description=criterion.description,
            measurement=criterion.measurement,
            output=str(output),
            context=str(context),
        )

        try:
            if not self.uses_llm:
                return self._heuristic_verification(criterion, output, context)

            assert self.llm_client is not None, "llm_client must be available when uses_llm is True"
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt=_prompts.get("builder_agent/verifier_llm_verification_system.md"),
            )

            # Parse response (simplified)
            if "SCORE:" in response:
                parts = response.split("|")
                score_part = parts[0].split("SCORE:")[1].strip()
                score = float(score_part)
                evidence = parts[1].split("EVIDENCE:")[1].strip() if len(parts) > 1 else response
                return max(0.0, min(1.0, score)), evidence

        except Exception as e:
            logger.warning("LLM verification failed, falling back to heuristic: %s", e)

        # Fallback to heuristic
        return self._heuristic_verification(criterion, output, context)

    async def _check_anti_criterion(
        self, anti_criterion: AntiCriterion, output: Any, context: dict
    ) -> AntiCriterionViolation | None:
        """Check if an anti-criterion is violated."""
        output_str = str(output).lower()

        # Heuristic checks for common violations
        violated = False
        description = ""

        if "verbose" in anti_criterion.description.lower() and len(output_str) > 5000:
            violated = True
            description = f"Output too verbose ({len(output_str)} chars)"

        if "fluff" in anti_criterion.description.lower():
            # Check for fluff words
            fluff_words = ["very", "really", "actually", "basically", "literally"]
            fluff_count = sum(output_str.count(word) for word in fluff_words)
            if fluff_count > 10:
                violated = True
                description = f"Contains {fluff_count} fluff words"

        if "error" in anti_criterion.description.lower() and context.get("errors"):
            violated = True
            description = f"Errors detected: {len(context['errors'])}"

        if violated:
            penalty = anti_criterion.severity * 0.1  # Up to 0.2 penalty
            return AntiCriterionViolation(
                anti_criterion_id=anti_criterion.id,
                severity=anti_criterion.severity,
                description=description,
                penalty=penalty,
            )

        return None

    def _calculate_overall_score(
        self,
        criterion_scores: list[CriterionScore],
        violations: list[AntiCriterionViolation],
        ideal_state: IdealState,
    ) -> float:
        """Calculate weighted overall score."""
        if not criterion_scores:
            return 0.0

        # Weighted average of criterion scores
        total_weight = sum(
            c.weight for c in ideal_state.success_criteria
            if any(cs.criterion_id == c.id for cs in criterion_scores)
        )

        if total_weight == 0:
            total_weight = len(criterion_scores)

        weighted_sum = 0.0
        for score in criterion_scores:
            criterion = next(
                (c for c in ideal_state.success_criteria if c.id == score.criterion_id),
                None
            )
            weight = criterion.weight if criterion else 1.0
            weighted_sum += score.score * weight

        base_score = weighted_sum / total_weight

        # Apply penalties from violations
        total_penalty = sum(v.penalty for v in violations)
        final_score = max(0.0, base_score - total_penalty)

        return round(final_score, 3)

    def _get_minimum_score(self, ideal_state: IdealState) -> float:
        """Get minimum acceptable score from stop conditions."""
        for condition in ideal_state.stop_conditions:
            if condition.condition_type == "min_score":
                return condition.threshold
        return 0.8  # Default

    def _identify_gaps(self, criterion_scores: list[CriterionScore]) -> list[str]:
        """Identify gaps (unmet criteria) with detailed evidence."""
        gaps = []
        for score in criterion_scores:
            if not score.passed:
                # Include both notes (target/achieved) and evidence for context
                gap_detail = f"{score.criterion_id}: {score.notes}"
                if score.evidence and len(score.evidence) > 10:
                    # Truncate long evidence but include key details
                    evidence_preview = score.evidence[:200]
                    if len(score.evidence) > 200:
                        evidence_preview += "..."
                    gap_detail += f" | Evidence: {evidence_preview}"
                gaps.append(gap_detail)
        return gaps

    def _suggest_next_actions(
        self, criterion_scores: list[CriterionScore], gaps: list[str]
    ) -> list[str]:
        """Suggest next actions based on gaps."""
        if not gaps:
            return ["Output meets criteria"]

        actions = []
        for score in criterion_scores:
            if not score.passed:
                actions.append(
                    f"Improve {score.criterion_id}: {score.evidence}"
                )

        return actions[:3]  # Top 3 actions

    def _generate_summary(
        self,
        overall_score: float,
        passed: bool,
        criterion_scores: list[CriterionScore],
        violations: list[AntiCriterionViolation],
        gaps: list[str],
    ) -> str:
        """Generate human-readable summary."""
        status = "PASSED" if passed else "NEEDS IMPROVEMENT"

        passed_count = sum(1 for s in criterion_scores if s.passed)
        total_count = len(criterion_scores)

        summary_parts = [
            f"Verification {status}: Overall score {overall_score:.2f}",
            f"Criteria met: {passed_count}/{total_count}",
        ]

        if violations:
            summary_parts.append(
                f"Anti-criteria violations: {len(violations)}"
            )

        if gaps:
            summary_parts.append(
                f"Key gaps: {', '.join(gaps[:2])}"
            )

        return " | ".join(summary_parts)
