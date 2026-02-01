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
import time
from typing import Any, Optional
from pydantic import BaseModel, Field

from .ideal_state import IdealState, SuccessCriterion, AntiCriterion
from .execution_schemas import (
    ExecutionResult,
    ExecutionStatus,
    ValidationResultV2,
    VerificationReportV2,
    CriterionEvidence,
)
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


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
        from ..runtime.time_utils import utcnow_isoformat

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

        # Identify gaps
        gaps = [
            f"Criterion '{ideal_state.success_criteria[i].description}' not met (score: {e.score:.2f})"
            for i, e in enumerate(criterion_evidences)
            if not e.passed
        ]

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
                score, llm_assessment = await self._llm_score_criterion(
                    criterion, exec_data, valid_data
                )
                evidence.score = score
                evidence.llm_assessment = llm_assessment
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
        criterion: SuccessCriterion,
    ) -> tuple[float, str, str]:
        """
        BL-006: Mechanically score functional criteria using structured data.
        """
        exec_evidence = ""
        valid_evidence = ""
        score = 0.0

        if exec_data:
            # Check smoke test passed
            if exec_data.get("smoke_test_passed"):
                score += 0.4
                exec_evidence += "Smoke test passed. "

            # Check all prompts passed
            if exec_data.get("all_prompts_passed"):
                score += 0.4
                exec_evidence += "All prompts passed. "
            elif exec_data.get("status") == "success":
                score += 0.3
                exec_evidence += "Execution succeeded. "
            elif exec_data.get("status") == "partial":
                score += 0.2
                exec_evidence += "Execution partially succeeded. "

            # Check for errors
            errors = exec_data.get("all_errors", [])
            if not errors:
                score += 0.2
                exec_evidence += "No errors. "
            else:
                exec_evidence += f"{len(errors)} errors found. "

        if valid_data:
            # Check validation findings
            error_count = valid_data.get("error_count", 0)
            if error_count == 0:
                valid_evidence += "No validation errors. "
            else:
                valid_evidence += f"{error_count} validation errors. "
                score -= 0.1 * min(error_count, 3)  # Penalty for errors

        return min(max(score, 0.0), 1.0), exec_evidence.strip(), valid_evidence.strip()

    def _mechanical_performance_score(
        self,
        exec_data: dict | None,
        criterion: SuccessCriterion,
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
        """Heuristic fallback for subjective criteria."""
        score = 0.5

        if exec_data:
            if exec_data.get("smoke_test_passed"):
                score += 0.2
            if exec_data.get("status") == "success":
                score += 0.2

        if valid_data:
            if valid_data.get("error_count", 0) == 0:
                score += 0.1

        return min(score, 1.0)

    async def _llm_score_criterion(
        self,
        criterion: SuccessCriterion,
        exec_data: dict | None,
        valid_data: dict | None,
    ) -> tuple[float, str]:
        """Use LLM to score subjective criterion."""
        if not self.llm_client:
            return 0.5, "LLM not available"

        # Build context from data
        context_parts = [f"Criterion: {criterion.description}"]

        if exec_data:
            context_parts.append(f"Execution status: {exec_data.get('status', 'unknown')}")
            context_parts.append(f"Smoke test passed: {exec_data.get('smoke_test_passed', 'unknown')}")
            errors = exec_data.get('all_errors', [])
            if errors:
                context_parts.append(f"Errors: {'; '.join(errors[:3])}")

        if valid_data:
            findings = valid_data.get('findings', [])
            if findings:
                finding_msgs = [f.get('message', '') for f in findings[:3]]
                context_parts.append(f"Validation findings: {'; '.join(finding_msgs)}")

        prompt = f"""Score this criterion from 0.0 to 1.0 based on the evidence:

{chr(10).join(context_parts)}

Provide a score (0.0-1.0) and brief explanation.
Format: SCORE: X.X | EXPLANATION: <text>"""

        try:
            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt="You are evaluating software quality criteria. Be objective.",
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

            return score, explanation

        except Exception as e:
            logger.warning(f"LLM scoring failed: {e}")
            return 0.5, f"LLM scoring error: {e}"

    def _check_anti_criterion_with_data(
        self,
        anti_criterion: AntiCriterion,
        exec_data: dict | None,
        valid_data: dict | None,
    ) -> dict | None:
        """Check if anti-criterion is violated using structured data."""

        description_lower = anti_criterion.description.lower()

        # Check for common anti-criteria patterns
        if "error" in description_lower or "fail" in description_lower:
            if exec_data and exec_data.get("all_errors"):
                return {
                    "anti_criterion_id": anti_criterion.id,
                    "severity": anti_criterion.severity,
                    "description": f"Errors present: {len(exec_data['all_errors'])} errors",
                    "penalty": 0.1 * anti_criterion.severity,
                }

        if "timeout" in description_lower:
            if exec_data:
                duration = exec_data.get("total_duration_ms", 0)
                if duration > 120000:  # > 2 minutes
                    return {
                        "anti_criterion_id": anti_criterion.id,
                        "severity": anti_criterion.severity,
                        "description": f"Execution exceeded timeout threshold: {duration}ms",
                        "penalty": 0.15 * anti_criterion.severity,
                    }

        if "crash" in description_lower or "exception" in description_lower:
            if exec_data:
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
        gaps: list[str],
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
        context: Optional[dict[str, Any]] = None,
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

        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        if tracer:
            span = tracer.start_span(
                "verify",
                attributes={
                    "iteration": iteration,
                    "criteria_count": len(ideal_state.success_criteria),
                    "anti_criteria_count": len(ideal_state.anti_criteria),
                    "uses_llm": self.uses_llm,
                },
            )

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

            # Record telemetry
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("verify_duration_seconds", duration)
                metrics.record_histogram("verify_score", overall_score)
                metrics.increment("verify_total", {"passed": str(passed).lower()})
                if violations:
                    metrics.increment("verify_violations_total", {"count": str(len(violations))})

            if span:
                span.set_attribute("overall_score", overall_score)
                span.set_attribute("passed", passed)
                span.set_attribute("criteria_passed", sum(1 for s in criterion_scores if s.passed))
                span.set_attribute("violation_count", len(violations))
                span.set_attribute("gap_count", len(gaps))
                span.set_attribute("duration_seconds", duration)
                span.add_event("verification_complete", {"score": overall_score, "passed": passed})
                span.end()

            return report

        except Exception as e:
            if span:
                span.record_exception(e)
                span.end()
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
        self, criterion: SuccessCriterion, output: Any, context: dict
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
        self, criterion: SuccessCriterion, output: Any, context: dict
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
        prompt = f"""Score this output against the criterion:

CRITERION: {criterion.description}
MEASUREMENT: {criterion.measurement}

OUTPUT:
{output}

CONTEXT:
{context}

Rate from 0.0 to 1.0 and explain your reasoning.
Format: SCORE: X.XX | EVIDENCE: explanation"""

        try:
            if not self.uses_llm:
                return self._heuristic_verification(criterion, output, context)

            response = await self.llm_client.generate(
                prompt=prompt,
                system_prompt="You are an objective quality evaluator.",
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
    ) -> Optional[AntiCriterionViolation]:
        """Check if an anti-criterion is violated."""
        output_str = str(output).lower()

        # Heuristic checks for common violations
        violated = False
        description = ""

        if "verbose" in anti_criterion.description.lower():
            # Check for excessive verbosity
            if len(output_str) > 5000:
                violated = True
                description = f"Output too verbose ({len(output_str)} chars)"

        if "fluff" in anti_criterion.description.lower():
            # Check for fluff words
            fluff_words = ["very", "really", "actually", "basically", "literally"]
            fluff_count = sum(output_str.count(word) for word in fluff_words)
            if fluff_count > 10:
                violated = True
                description = f"Contains {fluff_count} fluff words"

        if "error" in anti_criterion.description.lower():
            # Check for errors in context
            if context.get("errors"):
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
        """Identify gaps (unmet criteria)."""
        gaps = []
        for score in criterion_scores:
            if not score.passed:
                gaps.append(
                    f"{score.criterion_id}: {score.notes}"
                )
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
