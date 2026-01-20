"""
Verifier - Scores outputs against ideal state criteria.

Implements the VERIFY phase of the inner loop per loop.spec.md.
Every iteration produces:
- Score per criterion
- Pass/fail summary
- Evidence or heuristic notes
- Gaps and next actions
"""

import logging
from typing import Any, Optional
from pydantic import BaseModel, Field

from .ideal_state import IdealState, SuccessCriterion, AntiCriterion
from ..runtime.time_utils import utcnow

logger = logging.getLogger(__name__)


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
    """Verifies outputs against ideal state criteria."""

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def verify(
        self,
        ideal_state: IdealState,
        output: Any,
        iteration: int,
        context: Optional[dict[str, Any]] = None,
    ) -> VerificationReport:
        """
        Verify output against ideal state.

        Args:
            ideal_state: The target state with criteria
            output: The candidate output to verify
            iteration: Current iteration number
            context: Additional context (e.g., tool results, logs)

        Returns:
            VerificationReport with scores and analysis
        """
        from ..runtime.time_utils import utcnow_isoformat

        context = context or {}
        criterion_scores = []
        violations = []

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

        return VerificationReport(
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
        elif self.llm_client:
            score, evidence = await self._llm_based_verification(
                criterion, output, context
            )
        else:
            # Fallback to basic heuristic
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
            if self.llm_client is None:
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
            logger.warning(f"LLM verification failed: {e}")

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
