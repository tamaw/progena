"""
VERIFY Node - Verification phase of inner loop (loop.spec.md).

Scores candidate output against ideal state criteria.
Produces verification report with scores, gaps, next actions.
Tracks artefacts and best artefact per loop.spec.md.

Also detects when Copilot CLI needs clarification from the user.
"""

import logging
from typing import Callable, Coroutine, Any
import time

from ..state.models import ProgenitorState
from ..state.verifier import Verifier
from ..state.clarification import ClarificationDetector, create_clarification_history_entry
from ..runtime.time_utils import utcnow_isoformat

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
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the verify node function."""

    verifier = Verifier(llm_client)
    clarification_detector = ClarificationDetector(llm_client)

    async def verify_node(state: ProgenitorState) -> ProgenitorState:
        """
        VERIFY phase: Score output against ideal state.

        Per loop.spec.md, every iteration produces:
        - Score per criterion
        - Pass/fail summary
        - Evidence or heuristic notes
        - Gaps and next actions

        Also tracks artefacts and best_artefact for early-stop scenarios.

        Additionally checks if Copilot CLI output indicates need for
        user clarification, which can interrupt the inner loop.
        """
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        ideal_state = state.get("ideal_state")
        iteration = state.get("current_iteration", 0)

        if tracer:
            span = tracer.start_span(
                "node_verify",
                attributes={
                    "iteration": iteration,
                    "has_ideal_state": ideal_state is not None,
                },
            )

        if not ideal_state:
            logger.warning("No ideal state in VERIFY phase")
            if span:
                span.set_attribute("skipped", True)
                span.end()
            return {}

        iteration_start_time = state.get("iteration_start_time", time.time())

        # Get the output to verify
        output = state.get("last_iteration_output") or state.get("pending_response")

        # Gather context for verification
        context = {
            "validation_result": state.get("last_validation_result"),
            "errors": state.get("errors", []),
            "completed": state.get("work_item_completed", False),
            "tool_calls": state.get("actions_this_cycle", 0),
            "elapsed_time": time.time() - iteration_start_time,
        }

        logger.info(f"Verifying iteration {iteration} output")

        # Initialize clarification state updates
        clarification_updates = {
            "clarification_needed": False,
            "clarification_question": None,
            "clarification_context": None,
        }

        try:
            # Check for clarification needs from Copilot output
            # Only check if we have build output with Copilot results
            if output and isinstance(output, dict):
                build_result = output.get("result") or output.get("build_result")
                if build_result and isinstance(build_result, dict):
                    # Get task description for context
                    task_description = ""
                    if ideal_state:
                        if hasattr(ideal_state, "task_description"):
                            task_description = ideal_state.task_description
                        elif isinstance(ideal_state, dict):
                            task_description = ideal_state.get("task_description", "")

                    # Detect if clarification is needed
                    clarification_result = await clarification_detector.detect(
                        copilot_output=build_result,
                        task_description=task_description,
                        ideal_state=ideal_state,
                    )

                    if clarification_result.needs_clarification:
                        logger.info(
                            f"Clarification needed: {clarification_result.category} "
                            f"(confidence: {clarification_result.confidence:.2f})"
                        )

                        # Format the question for the user
                        formatted_question = await clarification_detector.format_user_question(
                            clarification_result,
                            task_description,
                        )

                        # Update clarification history
                        history = list(state.get("clarification_history", []))
                        history.append(create_clarification_history_entry(clarification_result))

                        clarification_updates = {
                            "clarification_needed": True,
                            "clarification_question": formatted_question,
                            "clarification_context": clarification_result.context,
                            "awaiting_clarification": True,
                            "clarification_history": history,
                        }

                        # Log clarification detection
                        if state_manager:
                            await state_manager.log_decision({
                                "event": "clarification_detected",
                                "iteration": iteration,
                                "category": clarification_result.category,
                                "confidence": clarification_result.confidence,
                                "question_preview": formatted_question[:200] if formatted_question else None,
                                "timestamp": utcnow_isoformat(),
                            })

                        if span:
                            span.add_event("clarification_detected", {
                                "category": clarification_result.category,
                                "confidence": clarification_result.confidence,
                            })

            verification_report = await verifier.verify(
                ideal_state=ideal_state,
                output=output,
                iteration=iteration,
                context=context,
            )

            # Track artefacts (loop.spec.md Task State requirement)
            artefacts = list(state.get("artefacts", []))
            current_artefact = {
                "iteration": iteration,
                "output": str(output)[:1000] if output else None,  # Truncate for storage
                "score": verification_report.overall_score,
                "passed": verification_report.passed,
                "timestamp": utcnow_isoformat(),
            }
            artefacts.append(current_artefact)

            # Track best artefact (for early-stop scenarios per spec)
            best_artefact = state.get("best_artefact")
            best_score = state.get("best_artefact_score", 0.0)

            if verification_report.overall_score > best_score:
                best_artefact = current_artefact
                best_score = verification_report.overall_score
                logger.info(f"New best artefact at iteration {iteration}: score={best_score:.2f}")

            # Track all verification reports (loop.spec.md Task State)
            verification_reports = list(state.get("verification_reports", []))
            if hasattr(verification_report, "model_dump"):
                report_dict = verification_report.model_dump()
            else:
                report_dict = verification_report if isinstance(verification_report, dict) else {}
            verification_reports.append(report_dict)

            # Log verification
            if state_manager:
                await state_manager.log_decision({
                    "event": "verification",
                    "iteration": iteration,
                    "score": verification_report.overall_score,
                    "passed": verification_report.passed,
                    "gaps_count": len(verification_report.gaps) if hasattr(verification_report, "gaps") else 0,
                    "is_best": best_score == verification_report.overall_score,
                    "timestamp": utcnow_isoformat(),
                })

            # Record telemetry
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "verify"})
                metrics.record_histogram("verification_score_distribution", verification_report.overall_score)
                metrics.increment("node_invocations_total", {"node": "verify", "success": "true"})
                if verification_report.passed:
                    metrics.increment("verification_passed_total")
                else:
                    metrics.increment("verification_failed_total")

            if span:
                span.set_attribute("score", verification_report.overall_score)
                span.set_attribute("passed", verification_report.passed)
                span.set_attribute("is_best", best_score == verification_report.overall_score)
                span.set_attribute("duration_seconds", duration)
                span.add_event("verification_complete", {
                    "score": verification_report.overall_score,
                    "passed": verification_report.passed,
                })
                span.end()

            logger.info(
                f"Verification complete: score={verification_report.overall_score:.2f}, "
                f"passed={verification_report.passed}"
            )

            # Merge verification results with clarification updates
            # Note: TypedDict doesn't support dynamic spread, but this is valid for LangGraph
            return {  # type: ignore[return-value]
                "last_verification_report": verification_report,
                "verification_passed": verification_report.passed,
                "verification_score": verification_report.overall_score,
                "artefacts": artefacts,
                "best_artefact": best_artefact,
                "best_artefact_score": best_score,
                "verification_reports": verification_reports,
                **clarification_updates,
            }

        except Exception as e:
            logger.error(f"VERIFY phase error: {e}", exc_info=True)
            if metrics:
                metrics.increment("node_invocations_total", {"node": "verify", "success": "false"})
            if span:
                span.record_exception(e)
                span.end()
            # Fallback - assume partial success
            return {
                "last_verification_report": {
                    "overall_score": 0.5,
                    "passed": False,
                    "summary": f"Verification error: {e}",
                    "gaps": ["Verification failed"],
                },
                "verification_passed": False,
                "verification_score": 0.5,
                "clarification_needed": False,
            }

    return verify_node
