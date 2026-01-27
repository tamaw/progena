"""
OBSERVE Node - First phase of inner loop (loop.spec.md).

Refines ideal state, adds anti-criteria, tightens criteria based on:
- Current iteration results
- Verification feedback
- New context or constraints discovered
"""

import logging
import time
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState
from ..state.ideal_state import IdealStateBuilder
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
    """Create the observe node function."""

    ideal_state_builder = IdealStateBuilder(llm_client)

    async def observe_node(state: ProgenitorState) -> ProgenitorState:
        """
        OBSERVE phase: Refine ideal state based on iteration results.

        - If first iteration: Build initial ideal state from task
        - Otherwise: Refine based on previous verification results
        """
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        iteration = state.get("current_iteration", 0)
        ideal_state = state.get("ideal_state")

        # Initialize inner loop state if this is the first entry
        # (moved from routing function to keep routing pure)
        state_updates = {}
        if iteration == 0 and not state.get("task_id"):
            import uuid
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
            }
            logger.info("Initialized inner loop state for task %s", state_updates["task_id"])

        if tracer:
            span = tracer.start_span(
                "node_observe",
                attributes={
                    "iteration": iteration,
                    "has_ideal_state": ideal_state is not None,
                    "is_initial": iteration == 0 or ideal_state is None,
                },
            )

        try:
            if iteration == 0 or ideal_state is None:
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
                span.set_attribute("criteria_count", len(ideal_state.success_criteria) if ideal_state else 0)
                span.end()

            # Merge state updates with ideal_state
            return {**state_updates, "ideal_state": ideal_state}

        except Exception as e:
            if metrics:
                metrics.increment("node_invocations_total", {"node": "observe", "success": "false"})
            if span:
                span.record_exception(e)
                span.end()
            raise

    return observe_node
