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
from typing import cast

from ..constants import DEFAULT_MAX_INNER_ITERATIONS, DEFAULT_VERIFICATION_TARGET_SCORE
from ..runtime.time_utils import utcnow_isoformat
from ..state.helpers import get_ideal_state, set_ideal_state
from ..state.ideal_state import IdealState, IdealStateBuilder
from ..state.models import ObservePhase, ProgenitorState
from .types import NodeFunction, NodeStateUpdate

logger = logging.getLogger(__name__)


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
        logger.info("OBSERVE: Starting observe phase")

        iteration = state.get("iteration", {}).get("current", 0)
        ideal_state = get_ideal_state(state)

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
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            now_time = time.time()
            now_iso = utcnow_isoformat()

            state_updates = {
                # Top-level build control
                "build_in_progress": True,
                "build_started_at": now_iso,
                "build_phase": "observe",
                "task_id": task_id,
                "should_iterate": True,

                # Nested task info (description + capabilities)
                "task": {
                    "description": task_description[:200],
                    "target_capabilities": [],
                    "created_at": now_iso,
                },
                # Nested iteration info (single source of truth)
                "iteration": {
                    "current": 0,
                    "max": DEFAULT_MAX_INNER_ITERATIONS,
                    "start_time": now_time,
                },
                # Initialize verification state
                "verification": {
                    "current_score": 0.0,
                    "current_passed": False,
                    "target_score": DEFAULT_VERIFICATION_TARGET_SCORE,
                    "current_report": None,
                    "gaps": [],
                },
                # Initialize history
                "history": {
                    "artefacts": [],
                    "verification_reports": [],
                    "lessons_learned": [],
                    "messages": [],
                },
                # Initialize best result
                "best": {
                    "iteration": None,
                    "score": 0.0,
                    "passed": False,
                },
                # Initialize clarification
                "clarification": {
                    "needed": False,
                    "question": None,
                    "context": None,
                    "awaiting": False,
                    "response": None,
                    "history": [],
                },
            }
            logger.info("Initialized inner loop state for task %s (build_in_progress=True)", task_id)
        else:
            # Increment iteration at the start of a new cycle when verification did not meet target
            if not state.get("clarification", {}).get("needed", False) and not awaiting_clarification:
                verification = state.get("verification", {})
                verification_score: float = verification.get("current_score") or 0.0
                target_score: float = verification.get("target_score") or DEFAULT_VERIFICATION_TARGET_SCORE
                if verification_score < target_score and not verification.get("current_passed", False):
                    next_iteration = (iteration or 0) + 1
                    # Preserve max from state (LangGraph shallow-merges, so we need to include it)
                    iter_info = state.get("iteration", {})
                    max_iter = iter_info.get("max", DEFAULT_MAX_INNER_ITERATIONS)
                    state_updates = {
                        # Nested iteration info (single source of truth)
                        "iteration": {
                            "current": next_iteration,
                            "max": max_iter,  # Preserve max_iterations through shallow merge
                            "start_time": time.time(),
                        },
                    }
                    iteration = next_iteration
                    logger.info("Incrementing iteration from %d to %d (score=%.2f < target=%.2f)",
                               next_iteration - 1, next_iteration, verification_score, target_score)

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

                # Enrich task context with build_settings if available
                # This provides the LLM-refined description from the decide step
                build_settings = state.get("build_settings")
                if build_settings:
                    bs_description = build_settings.get("description") if isinstance(build_settings, dict) else getattr(build_settings, "description", None)
                    bs_project_name = build_settings.get("project_name") if isinstance(build_settings, dict) else getattr(build_settings, "project_name", None)
                    build_settings.get("user_request") if isinstance(build_settings, dict) else getattr(build_settings, "user_request", None)

                    # If we have a refined description, use it to enhance the task
                    if bs_description and bs_description != task:
                        task = f"{task}\n\n**Build Description:** {bs_description}"
                    if bs_project_name:
                        task = f"{task}\n\n**Project Name:** {bs_project_name}"

                    logger.info("Enriched task with build_settings: project=%s", bs_project_name)

                context = {
                    "messages": state.get("messages", []),
                    "backlog": state.get("backlog", []),
                    "capabilities": state.get("available_capabilities", []),
                    "build_settings": build_settings,  # Include for additional context
                }

                logger.info("Building initial ideal state")
                ideal_state = await ideal_state_builder.build_from_task(task, context)

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
                last_verification = state.get("verification", {}).get("current_report")
                last_output = state.get("last_iteration_output")

                if last_verification:
                    logger.info(f"Refining ideal state (iteration {iteration})")

                    # Handle both dict and VerificationReport objects
                    if isinstance(last_verification, dict):
                        verification_dict = last_verification
                    elif hasattr(last_verification, "model_dump"):
                        verification_dict = last_verification.model_dump()
                    else:
                        verification_dict = {"gaps": [], "anti_criterion_violations": []}

                    iteration_results = {
                        "verification": verification_dict,
                        "output": last_output,
                        "gaps": verification_dict.get("gaps", []),
                        "violations": verification_dict.get("anti_criterion_violations", []),
                        "learning_summary": state.get("learning_summary"),
                    }

                    ideal_state = await ideal_state_builder.refine_from_iteration(
                        ideal_state, iteration_results
                    )

                    if state_manager:
                        await state_manager.log_decision({
                            "event": "ideal_state_refined",
                            "version": ideal_state.version,
                            "iteration": iteration,
                            "timestamp": utcnow_isoformat(),
                        })

            # Merge state updates with ideal_state and clarification updates
            # Always update build_phase to show current phase
            if isinstance(ideal_state, IdealState):
                ideal_state_dict = ideal_state.model_dump()
            elif isinstance(ideal_state, dict):
                ideal_state_dict = ideal_state
            else:
                ideal_state_dict = None
            phases = set_ideal_state(state.get("phases"), ideal_state_dict)
            observe_phase = cast(ObservePhase, dict(phases.get("observe") or {}))
            observe_phase["completed_at"] = utcnow_isoformat()
            phases["observe"] = observe_phase

            return {
                **state_updates,
                **clarification_updates,
                "phases": phases,
                "build_phase": "observe",
                "learning_summary": None,  # Clear after consumption
            }

        except Exception:
            raise

    return observe_node
