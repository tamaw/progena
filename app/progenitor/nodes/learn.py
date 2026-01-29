"""
LEARN Node - Learning phase of inner loop (loop.spec.md).

Updates strategy based on verification results.
Captures lessons learned for future iterations.
"""

import logging
import time
from typing import Any

from ..state.models import ProgenitorState
from ..runtime.time_utils import utcnow_isoformat
from . import NodeFunction, NodeStateUpdate

logger = logging.getLogger(__name__)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


def create_node(
    llm_client,
    state_manager=None,
) -> NodeFunction:
    """Create the learn node function.

    CQ-006: Returns NodeFunction type - async function that takes
    ProgenitorState and returns NodeStateUpdate (partial dict).
    """

    async def learn_node(state: ProgenitorState) -> NodeStateUpdate:
        """
        LEARN phase: Update strategy and capture lessons.

        Analyzes:
        - What worked (successful criteria)
        - What didn't work (gaps, violations)
        - What to try differently next iteration
        - Patterns to remember
        """
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        iteration = state.get("current_iteration", 0)
        verification_report = state.get("last_verification_report")

        if tracer:
            span = tracer.start_span(
                "node_learn",
                attributes={
                    "iteration": iteration,
                    "has_verification": verification_report is not None,
                },
            )

        if not verification_report:
            logger.warning("No verification report in LEARN phase")
            if span:
                span.set_attribute("skipped", True)
                span.end()
            return {}

        report_dict = verification_report.model_dump() if hasattr(verification_report, "model_dump") else verification_report

        # Extract lessons
        lessons = {
            "iteration": iteration,
            "what_worked": [],
            "what_failed": [],
            "adjustments": [],
            "insights": [],
        }

        # Analyze criterion scores
        for score in report_dict.get("criterion_scores", []):
            if score.get("passed"):
                lessons["what_worked"].append({
                    "criterion": score.get("criterion_id"),
                    "evidence": score.get("evidence"),
                })
            else:
                lessons["what_failed"].append({
                    "criterion": score.get("criterion_id"),
                    "gap": score.get("notes"),
                })

        # Analyze violations
        for violation in report_dict.get("anti_criterion_violations", []):
            lessons["what_failed"].append({
                "type": "violation",
                "description": violation.get("description"),
            })

        # Generate adjustments
        gaps = report_dict.get("gaps", [])
        if gaps:
            lessons["adjustments"] = [
                f"Address gap: {gap}" for gap in gaps[:3]
            ]

        # Use LLM for deeper insights (if available)
        if llm_client and iteration > 0:
            try:
                insights = await _generate_insights(
                    llm_client, iteration, report_dict, state
                )
                lessons["insights"] = insights
                if span:
                    span.add_event("insights_generated", {"count": len(insights)})
            except Exception as e:
                logger.warning(f"Could not generate insights: {e}")

        # Store lessons
        all_lessons = state.get("lessons_learned", [])
        all_lessons.append(lessons)

        # Log learning
        if state_manager:
            await state_manager.log_decision({
                "event": "learning_phase",
                "iteration": iteration,
                "worked_count": len(lessons["what_worked"]),
                "failed_count": len(lessons["what_failed"]),
                "adjustments_count": len(lessons["adjustments"]),
                "timestamp": utcnow_isoformat(),
            })

        # Record telemetry
        duration = time.perf_counter() - start_time
        if metrics:
            metrics.record_histogram("node_duration_seconds", duration, {"node": "learn"})
            metrics.increment("node_invocations_total", {"node": "learn", "success": "true"})
            metrics.record_histogram("lessons_worked_count", len(lessons["what_worked"]))
            metrics.record_histogram("lessons_failed_count", len(lessons["what_failed"]))

        if span:
            span.set_attribute("worked_count", len(lessons["what_worked"]))
            span.set_attribute("failed_count", len(lessons["what_failed"]))
            span.set_attribute("adjustments_count", len(lessons["adjustments"]))
            span.set_attribute("insights_count", len(lessons["insights"]))
            span.set_attribute("duration_seconds", duration)
            span.end()

        logger.info(
            f"Learning complete: {len(lessons['what_worked'])} successes, "
            f"{len(lessons['what_failed'])} failures"
        )

        # Increment iteration for next cycle
        next_iteration = iteration + 1

        return {
            "lessons_learned": all_lessons,
            "current_lesson": lessons,
            "current_iteration": next_iteration,  # Ready for next iteration
        }

    return learn_node


async def _generate_insights(llm_client, iteration: int, verification: dict, state) -> list[str]:
    """Use LLM to generate deeper insights from verification results."""

    prompt = f"""Analyze this iteration's verification results and extract insights:

ITERATION: {iteration}
SCORE: {verification.get('overall_score', 0):.2f}
PASSED: {verification.get('passed', False)}

WHAT WORKED:
{chr(10).join(f"- {s.get('criterion_id')}: {s.get('evidence')}" for s in verification.get('criterion_scores', []) if s.get('passed'))}

WHAT FAILED:
{chr(10).join(f"- {gap}" for gap in verification.get('gaps', []))}

Extract 2-3 key insights:
1. What patterns do you see?
2. What should we do differently?
3. What constraints should we add?

Be specific and actionable."""

    response = await llm_client.generate(
        prompt=prompt,
        system_prompt="You extract actionable insights from iteration results.",
    )

    # Parse insights (simplified)
    insights = []
    for line in response.split("\n"):
        line = line.strip()
        if line and (line[0].isdigit() or line.startswith("-")):
            insights.append(line.lstrip("0123456789.-) "))

    return insights[:3]
