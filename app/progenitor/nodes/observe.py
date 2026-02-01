"""
OBSERVE Node - First phase of inner loop (loop.spec.md).

Refines ideal state, adds anti-criteria, tightens criteria based on:
- Current iteration results
- Verification feedback
- New context or constraints discovered
- User clarification responses (when the build needed more info)
"""

import logging
import time
from typing import Any

from ..state.models import ProgenitorState
from ..state.ideal_state import IdealStateBuilder, IdealState
from ..state.clarification import create_clarification_history_entry
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
    """Create the observe node function.

    CQ-006: Returns NodeFunction type - async function that takes
    ProgenitorState and returns NodeStateUpdate (partial dict).
    """

    ideal_state_builder = IdealStateBuilder(llm_client)

    async def observe_node(state: ProgenitorState) -> NodeStateUpdate:
        """
        OBSERVE phase: Refine ideal state based on iteration results.

        - If first iteration: Build initial ideal state from task
        - If clarification response: Incorporate user's additional context
        - Otherwise: Refine based on previous verification results
        """
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        iteration = state.get("current_iteration", 0)
        ideal_state = state.get("ideal_state")

        # Convert ideal_state from dict to IdealState object if necessary
        # (LangGraph may serialize/deserialize state as dicts)
        if ideal_state is not None and isinstance(ideal_state, dict):
            try:
                ideal_state = IdealState(**ideal_state)
                logger.debug("Converted ideal_state from dict to IdealState object")
            except Exception as e:
                logger.warning(f"Failed to convert ideal_state dict to IdealState: {e}")
                ideal_state = None

        # Check if we have a clarification response to incorporate
        clarification_response = state.get("clarification_response")
        awaiting_clarification = state.get("awaiting_clarification", False)

        # Track clarification-related state updates
        clarification_updates = {}

        # Initialize inner loop state if this is the first entry
        # (moved from routing function to keep routing pure)
        state_updates = {}
        if iteration == 0 and not state.get("task_id"):
            import uuid
            task_description = state.get("current_input") or state.get("task_description", "Build task")
            state_updates = {
                "current_iteration": 0,
                "iteration_start_time": time.time(),
                "should_iterate": True,
                "lessons_learned": [],
                "task_id": f"task_{uuid.uuid4().hex[:8]}",
                "artefacts": [],
                "best_artefact": None,
                "best_artefact_score": 0.0,
                "verification_reports": [],
                # Build state tracking
                "build_in_progress": True,
                "build_started_at": utcnow_isoformat(),
                "build_task_description": task_description[:200],
                "build_messages": [],
                "build_phase": "observe",
            }
            logger.info("Initialized inner loop state for task %s (build_in_progress=True)", state_updates["task_id"])

        if tracer:
            span = tracer.start_span(
                "node_observe",
                attributes={
                    "iteration": iteration,
                    "has_ideal_state": ideal_state is not None,
                    "is_initial": iteration == 0 or ideal_state is None,
                    "has_clarification_response": clarification_response is not None,
                },
            )

        try:
            # Handle clarification response - incorporate user's additional context
            if awaiting_clarification and clarification_response and ideal_state:
                logger.info("Incorporating clarification response into ideal state")

                # Update the clarification history to mark it as resolved
                history = list(state.get("clarification_history", []))
                if history and not history[-1].get("resolved"):
                    history[-1]["user_response"] = clarification_response
                    history[-1]["resolved"] = True

                # Refine ideal state with clarification context
                clarification_context = {
                    "user_clarification": clarification_response,
                    "original_context": state.get("clarification_context", ""),
                    "iteration_before_clarification": iteration,
                }

                ideal_state = await ideal_state_builder.refine_with_clarification(
                    ideal_state, clarification_context
                )

                # Clear clarification state - ready for next iteration
                clarification_updates = {
                    "awaiting_clarification": False,
                    "clarification_response": None,
                    "clarification_context": None,
                    "clarification_question": None,
                    "clarification_history": history,
                }

                if span:
                    span.add_event("clarification_incorporated", {
                        "response_length": len(clarification_response),
                    })

                if state_manager:
                    await state_manager.log_decision({
                        "event": "clarification_incorporated",
                        "iteration": iteration,
                        "response_preview": clarification_response[:200] if clarification_response else None,
                        "timestamp": utcnow_isoformat(),
                    })

            elif iteration == 0 or ideal_state is None:
                # First iteration - build initial ideal state
                task = state.get("current_input") or state.get("task_description", "")
                context = {
                    "messages": state.get("messages", []),
                    "backlog": state.get("backlog", []),
                    "capabilities": state.get("available_capabilities", []),
                }

                logger.info("Building initial ideal state")
                ideal_state = await ideal_state_builder.build_from_task(task, context)

                if span:
                    span.add_event("ideal_state_created", {
                        "criteria_count": len(ideal_state.success_criteria),
                        "anti_criteria_count": len(ideal_state.anti_criteria),
                    })

                if state_manager:
                    await state_manager.log_decision({
                        "event": "ideal_state_created",
                        "version": ideal_state.version,
                        "criteria_count": len(ideal_state.success_criteria),
                        "anti_criteria_count": len(ideal_state.anti_criteria),
                        "timestamp": utcnow_isoformat(),
                    })

            else:
                # Subsequent iterations - refine based on results
                last_verification = state.get("last_verification_report")
                last_output = state.get("last_iteration_output")

                if last_verification:
                    logger.info(f"Refining ideal state (iteration {iteration})")

                    # Handle both dict and VerificationReport objects
                    if hasattr(last_verification, "model_dump"):
                        verification_dict = last_verification.model_dump()
                    elif isinstance(last_verification, dict):
                        verification_dict = last_verification
                    else:
                        verification_dict = {"gaps": [], "anti_criterion_violations": []}

                    iteration_results = {
                        "verification": verification_dict,
                        "output": last_output,
                        "gaps": verification_dict.get("gaps", []),
                        "violations": verification_dict.get("anti_criterion_violations", []),
                    }

                    ideal_state = await ideal_state_builder.refine_from_iteration(
                        ideal_state, iteration_results
                    )

                    if span:
                        span.add_event("ideal_state_refined", {
                            "version": ideal_state.version,
                            "gaps_processed": len(iteration_results.get("gaps", [])),
                        })

                    if state_manager:
                        await state_manager.log_decision({
                            "event": "ideal_state_refined",
                            "version": ideal_state.version,
                            "iteration": iteration,
                            "timestamp": utcnow_isoformat(),
                        })

            # Record metrics
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "observe"})
                metrics.increment("node_invocations_total", {"node": "observe", "success": "true"})

            if span:
                span.set_attribute("duration_seconds", duration)
                # Handle ideal_state being either IdealState object or dict
                if ideal_state:
                    if hasattr(ideal_state, "success_criteria"):
                        criteria_count = len(ideal_state.success_criteria)
                    elif isinstance(ideal_state, dict):
                        criteria_count = len(ideal_state.get("success_criteria", []))
                    else:
                        criteria_count = 0
                else:
                    criteria_count = 0
                span.set_attribute("criteria_count", criteria_count)
                span.end()

            # Merge state updates with ideal_state and clarification updates
            # Always update build_phase to show current phase
            return {
                **state_updates,
                **clarification_updates,
                "ideal_state": ideal_state,
                "build_phase": "observe",
            }

        except Exception as e:
            if metrics:
                metrics.increment("node_invocations_total", {"node": "observe", "success": "false"})
            if span:
                span.record_exception(e)
                span.end()
            raise

    return observe_node
