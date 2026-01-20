"""
VERIFY Node - Verification phase of inner loop (loop.spec.md).

Scores candidate output against ideal state criteria.
Produces verification report with scores, gaps, next actions.
Tracks artefacts and best artefact per loop.spec.md.
"""

import logging
from typing import Callable, Coroutine, Any
import time

from ..state.models import ProgenitorState
from ..state.verifier import Verifier
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the verify node function."""

    verifier = Verifier(llm_client)

    async def verify_node(state: ProgenitorState) -> ProgenitorState:
        """
        VERIFY phase: Score output against ideal state.

        Per loop.spec.md, every iteration produces:
        - Score per criterion
        - Pass/fail summary
        - Evidence or heuristic notes
        - Gaps and next actions

        Also tracks artefacts and best_artefact for early-stop scenarios.
        """
        ideal_state = state.get("ideal_state")
        if not ideal_state:
            logger.warning("No ideal state in VERIFY phase")
            return {}

        iteration = state.get("current_iteration", 0)
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

        try:
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

            logger.info(
                f"Verification complete: score={verification_report.overall_score:.2f}, "
                f"passed={verification_report.passed}"
            )

            return {
                "last_verification_report": verification_report,
                "verification_passed": verification_report.passed,
                "verification_score": verification_report.overall_score,
                "artefacts": artefacts,
                "best_artefact": best_artefact,
                "best_artefact_score": best_score,
                "verification_reports": verification_reports,
            }

        except Exception as e:
            logger.error(f"VERIFY phase error: {e}", exc_info=True)
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
            }

    return verify_node
