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
        logger.info("LEARN: Starting learn phase")
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        iteration = state.get("current_iteration", 0)
        verification_report = state.get("last_verification_report")

        # Fallback: check phases.verify if last_verification_report not set
        if not verification_report:
            phases = state.get("phases", {})
            verify_phase = phases.get("verify", {})
            if verify_phase:
                # Construct a minimal report from phases.verify data
                verification_report = {
                    "overall_score": verify_phase.get("score", 0.0),
                    "passed": verify_phase.get("passed", False),
                    "gaps": verify_phase.get("gaps", []),
                    "criterion_scores": [],
                    "anti_criterion_violations": [],
                }
                logger.info("LEARN: Using phases.verify data as fallback (score=%.2f)", verify_phase.get("score", 0.0))

        # Also check history.verification_reports for the latest
        if not verification_report:
            history = state.get("history", {})
            reports = history.get("verification_reports", [])
            if reports:
                verification_report = reports[-1]
                logger.info("LEARN: Using latest verification_report from history")

        if tracer:
            span = tracer.start_span(
                "node_learn",
                attributes={
                    "iteration": iteration,
                    "has_verification": verification_report is not None,
                },
            )

        if not verification_report:
            logger.warning("No verification report in LEARN phase - creating minimal lessons")
            # Still produce lessons from available state data
            execution_result = state.get("execution_result") or state.get("phases", {}).get("execute", {}).get("result")
            lessons = {
                "iteration": iteration,
                "what_worked": [],
                "what_failed": ["No verification data available"],
                "adjustments": ["Ensure verification phase produces report"],
                "insights": [],
            }
            if execution_result:
                status = execution_result.get("status", "unknown") if isinstance(execution_result, dict) else "unknown"
                lessons["what_failed"].append(f"Execution status: {status}")

            history = state.get("history", {})
            all_lessons = list(history.get("lessons_learned", []))
            all_lessons.append(lessons)

            if span:
                span.set_attribute("fallback_mode", True)
                span.end()

            return {
                "history": {
                    "artefacts": history.get("artefacts", []),
                    "verification_reports": history.get("verification_reports", []),
                    "lessons_learned": all_lessons,
                    "messages": history.get("messages", []),
                },
                "build_phase": "learn",
            }

        # Cast to dict if it's a Pydantic model, otherwise use as-is
        report_dict: dict[str, Any]
        if hasattr(verification_report, "model_dump"):
            report_dict = verification_report.model_dump()  # type: ignore[union-attr]
        else:
            report_dict = dict(verification_report) if verification_report else {}

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
        if llm_client and iteration is not None and iteration > 0:
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
        history = state.get("history", {})
        all_lessons = list(history.get("lessons_learned", []))
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
            f"{len(lessons['what_failed'])} failures, iteration={iteration}"
        )

        # Preserve full history structure while updating lessons_learned
        # This prevents overwriting artefacts, verification_reports, and messages
        full_history = {
            "artefacts": history.get("artefacts", []),
            "verification_reports": history.get("verification_reports", []),
            "lessons_learned": all_lessons,
            "messages": history.get("messages", []),
        }

        return {
            "history": full_history,
            "build_phase": "learn",
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
