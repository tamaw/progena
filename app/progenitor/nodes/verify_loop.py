"""
VERIFY Node - Verification phase of inner loop (loop.spec.md).

BL-006: Updated to use structured execution+validation data for mechanical scoring.

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
from ..state.execution_schemas import ExecutionResult, ValidationResultV2, VerificationReportV2
from ..state.helpers import get_ideal_state
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
        logger.info("VERIFY: Starting verify phase")
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        ideal_state = get_ideal_state(state)
        iteration = state.get("iteration", {}).get("current", 0)

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

        iteration_start_time = state.get("iteration", {}).get("start_time", time.time())

        # Get the output to verify - from phases.execute.result
        phases = state.get("phases", {})
        output = phases.get("execute", {}).get("result") or state.get("pending_response")

        # Gather context for verification
        context = {
            "validation_result": state.get("validation_result_v2"),
            "errors": state.get("errors", []),
            "completed": state.get("work_item_completed", False),
            "tool_calls": state.get("actions_this_cycle", 0),
            "elapsed_time": time.time() - iteration_start_time,
        }

        logger.info(f"Verifying iteration {iteration} output")

        # Initialize clarification state updates
        clarification_updates = {
            "clarification": {
                "needed": False,
                "question": None,
                "context": None,
            },
        }

        # Initialize verification_report to avoid possibly unbound errors
        verification_report: VerificationReportV2 | None = None
        verification_report_dict: dict[str, Any] | None = None

        try:
            # Check for clarification needs from Copilot output
            # Only check if we have build output with Copilot results
            if output and isinstance(output, dict):
                build_result = output.get("result") or output.get("build_result")
                if build_result and isinstance(build_result, dict):
                    # Get task description for context
                    task_description = ""
                    if ideal_state:
                        if isinstance(ideal_state, dict):
                            task_description = ideal_state.get("task_description", "")
                        elif hasattr(ideal_state, "task_description"):
                            task_description = ideal_state.task_description

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
                            "clarification": {
                                "needed": True,
                                "question": formatted_question,
                                "context": clarification_result.context,
                                "awaiting": True,
                                "history": history,
                            },
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

            # BL-006: Prefer structured data verification when available
            # Check both top-level and phases.execute.result for execution data
            execution_result = state.get("execution_result") or state.get("phases", {}).get("execute", {}).get("result")
            # BL-006 FIX: validate node stores as validation_result_v2, not validation_result
            validation_result = state.get("validation_result_v2") or state.get("validation_result")

            if execution_result or validation_result:
                # Use new structured verification method
                logger.info("Using structured data verification (BL-006)")
                verification_report = await verifier.verify_with_structured_data(
                    ideal_state=ideal_state,
                    execution_result=execution_result,
                    validation_result=validation_result,
                    iteration=iteration,
                )
                verification_report_dict = verification_report.model_dump()

            if not verification_report:
                # Fallback if no verification was performed
                logger.warning("No verification report generated, using default")
                verification_report = VerificationReportV2(
                    iteration=iteration,
                    overall_score=0.5,
                    passed=False,
                    gaps=["No verification performed"],
                    next_actions=["Gather execution/validation data"],
                    summary="No verification was performed due to missing execution or validation data.",
                )
                verification_report_dict = verification_report.model_dump()

            # Track artefacts (loop.spec.md Task State requirement)
            history = state.get("history", {})
            artefacts = list(history.get("artefacts", []))
            current_artefact: dict[str, Any] = {
                "iteration": iteration,
                "output": str(output)[:1000] if output else None,  # Truncate for storage
                "score": verification_report.overall_score,
                "passed": verification_report.passed,
                "timestamp": utcnow_isoformat(),
            }

            # BL-006: Add evidence citations to artefact if using V2 report
            if verification_report_dict and isinstance(verification_report_dict, dict) and "evidence_citations" in verification_report_dict:
                current_artefact["evidence_citations"] = verification_report_dict["evidence_citations"][:5]

            artefacts.append(current_artefact)

            # Track best artefact (for early-stop scenarios per spec)
            best = state.get("best", {})
            best_score = best.get("score", 0.0)
            best_artefact: dict[str, Any] | None = None

            if verification_report.overall_score > best_score:
                best_artefact = current_artefact
                best_score = verification_report.overall_score
                logger.info(f"New best artefact at iteration {iteration}: score={best_score:.2f}")

            # Track all verification reports (loop.spec.md Task State)
            verification_reports: list[dict[str, Any]] = list(history.get("verification_reports", []))
            if verification_report_dict:
                # Use the V2 dict we already prepared
                if isinstance(verification_report_dict, dict):
                    report_dict = verification_report_dict
                else:
                    report_dict = verification_report_dict.model_dump() if hasattr(verification_report_dict, "model_dump") else {}
            elif hasattr(verification_report, "model_dump"):
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

            # Build nested structure updates
            phases_verify = {
                "passed": verification_report.passed,
                "score": verification_report.overall_score,
                "target_score": state.get("target_score", 0.8),
                "gaps": verification_report.gaps if hasattr(verification_report, "gaps") else [],
            }

            best_update = {
                "iteration": best_artefact.get("iteration") if best_artefact else None,
                "score": best_score,
                "passed": best_artefact.get("passed", False) if best_artefact else False,
            }

            history_update = {
                "artefacts": artefacts,
                "verification_reports": verification_reports,
            }

            # Get current iteration from nested state
            current_iter = state.get("iteration", {}).get("current", 0)

            # Merge verification results with clarification updates
            # Use nested structure for state updates
            # Note: TypedDict doesn't support dynamic spread, but this is valid for LangGraph
            return {  # type: ignore[return-value]
                # Nested structure updates
                "phases": {
                    "verify": phases_verify,
                },
                "history": history_update,
                "best": best_update,
                "build_phase": "verify",
                # New nested structure for verification state
                "verification": {
                    "current_score": verification_report.overall_score,
                    "current_passed": verification_report.passed,
                    "current_report": verification_report,
                    "target_score": state.get("target_score", 0.8),
                    "gaps": verification_report.gaps if hasattr(verification_report, "gaps") else [],
                },
                "iteration": {
                    "current": current_iter,
                },
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
                "phases": {
                    "verify": {
                        "score": 0.5,
                        "passed": False,
                        "gaps": ["Verification failed"],
                    },
                },
                "clarification": {
                    "needed": False,
                },
                "build_phase": "verify",
            }

    return verify_node
