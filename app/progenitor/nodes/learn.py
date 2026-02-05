"""
LEARN Node - Learning phase of inner loop (loop.spec.md).

Analyzes whether the plan was achieved and whether we're progressing
toward the ideal state. Feeds all context to the LLM for holistic assessment.
"""

import json
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


def _serialize_for_prompt(obj: Any) -> str:
    """Serialize an object to JSON string for prompt inclusion."""
    if obj is None:
        return "None"
    if hasattr(obj, "model_dump"):
        return json.dumps(obj.model_dump(), indent=2, default=str)
    if isinstance(obj, dict):
        return json.dumps(obj, indent=2, default=str)
    return str(obj)


def _gather_full_context(state: ProgenitorState) -> dict[str, Any]:
    """Gather all relevant context from state for LLM analysis.

    Collects: ideal state, experiment plan, execution results,
    verification reports, previous lessons, and iteration info.
    """
    context = {}

    # Iteration info
    context["iteration"] = state.get("current_iteration", 0)
    context["user_request"] = state.get("user_request", "")

    # Ideal state - the target we're trying to reach
    ideal_state = state.get("ideal_state")
    if ideal_state:
        if hasattr(ideal_state, "model_dump"):
            context["ideal_state"] = ideal_state.model_dump()
        else:
            context["ideal_state"] = ideal_state

    # Experiment plan - what we planned to do this iteration
    phases = state.get("phases", {})
    plan_phase = phases.get("plan", {})
    experiment_plan = plan_phase.get("experiment_plan")
    if experiment_plan:
        if hasattr(experiment_plan, "model_dump"):
            context["experiment_plan"] = experiment_plan.model_dump()  # type: ignore[union-attr]
        else:
            context["experiment_plan"] = experiment_plan

    # Think phase - reasoning and approaches
    think_phase = phases.get("think", {})
    if think_phase:
        context["think_phase"] = {
            "approaches": think_phase.get("approaches", []),
            "recommended_id": think_phase.get("recommended_id"),
            "gaps_analyzed": think_phase.get("gaps_analyzed", []),
        }

    # Execution result - what actually happened
    execution_result = state.get("execution_result") or phases.get("execute", {}).get("result")
    if execution_result:
        if isinstance(execution_result, dict):
            context["execution_result"] = execution_result
        elif hasattr(execution_result, "model_dump"):
            context["execution_result"] = execution_result.model_dump()
        else:
            context["execution_result"] = {"raw": str(execution_result)}

    # Verification report - how well we did
    verification_report = state.get("last_verification_report")
    if not verification_report:
        verify_phase = phases.get("verify", {})
        if verify_phase:
            verification_report = {
                "overall_score": verify_phase.get("score", 0.0),
                "passed": verify_phase.get("passed", False),
                "gaps": verify_phase.get("gaps", []),
                "target_score": verify_phase.get("target_score", 0.8),
            }

    if verification_report:
        if hasattr(verification_report, "model_dump"):
            context["verification_report"] = verification_report.model_dump()  # type: ignore[union-attr]
        else:
            context["verification_report"] = verification_report

    # History - previous iterations
    history = state.get("history", {})
    context["previous_lessons"] = history.get("lessons_learned", [])
    context["previous_artefacts"] = history.get("artefacts", [])

    # Best result so far
    best = state.get("best")
    if best:
        context["best_result"] = best

    return context


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
        LEARN phase: Analyze if plan was achieved and progress toward ideal state.

        Gathers all context (plan, ideal state, build results, tests, verification)
        and feeds it to the LLM for holistic assessment of:
        - Was the experiment plan achieved?
        - Are we progressing toward the ideal state?
        - What worked, what failed, what to adjust?
        """
        logger.info("LEARN: Starting learn phase")
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        iteration = state.get("current_iteration", 0)

        if tracer:
            span = tracer.start_span(
                "node_learn",
                attributes={"iteration": iteration},
            )

        # Gather ALL context for LLM analysis
        context = _gather_full_context(state)

        # Generate lessons via LLM
        lessons = await _generate_lessons_via_llm(llm_client, context)
        lessons["iteration"] = iteration

        # Store lessons
        history = state.get("history", {})
        all_lessons = list(history.get("lessons_learned", []))
        all_lessons.append(lessons)

        # Log learning
        if state_manager:
            await state_manager.log_decision({
                "event": "learning_phase",
                "iteration": iteration,
                "plan_achieved": lessons.get("plan_achieved", False),
                "ideal_state_progress": lessons.get("ideal_state_progress", "unknown"),
                "worked_count": len(lessons.get("what_worked", [])),
                "failed_count": len(lessons.get("what_failed", [])),
                "timestamp": utcnow_isoformat(),
            })

        # Record telemetry
        duration = time.perf_counter() - start_time
        if metrics:
            metrics.record_histogram("node_duration_seconds", duration, {"node": "learn"})
            metrics.increment("node_invocations_total", {"node": "learn", "success": "true"})

        if span:
            span.set_attribute("plan_achieved", lessons.get("plan_achieved", False))
            span.set_attribute("ideal_state_progress", lessons.get("ideal_state_progress", "unknown"))
            span.set_attribute("duration_seconds", duration)
            span.end()

        logger.info(
            f"Learning complete: plan_achieved={lessons.get('plan_achieved')}, "
            f"progress={lessons.get('ideal_state_progress')}, iteration={iteration}"
        )

        # Preserve full history structure while updating lessons_learned
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


async def _generate_lessons_via_llm(llm_client, context: dict[str, Any]) -> dict[str, Any]:
    """Use LLM to analyze all context and generate comprehensive lessons.

    Feeds the full context (ideal state, plan, execution, verification)
    to the LLM for holistic assessment.
    """

    # Build the comprehensive prompt
    prompt = f"""Analyze this iteration and determine:
1. Was the EXPERIMENT PLAN achieved?
2. Are we progressing toward the IDEAL STATE?
3. What worked, what failed, and what adjustments to make?

=== FULL CONTEXT ===

ITERATION: {context.get('iteration', 0)}

USER REQUEST:
{context.get('user_request', 'Not provided')}

IDEAL STATE (our target):
{_serialize_for_prompt(context.get('ideal_state'))}

EXPERIMENT PLAN (what we planned this iteration):
{_serialize_for_prompt(context.get('experiment_plan'))}

THINK PHASE (reasoning and approaches):
{_serialize_for_prompt(context.get('think_phase'))}

EXECUTION RESULT (what actually happened):
{_serialize_for_prompt(context.get('execution_result'))}

VERIFICATION REPORT (how well we did):
{_serialize_for_prompt(context.get('verification_report'))}

PREVIOUS LESSONS (from earlier iterations):
{_serialize_for_prompt(context.get('previous_lessons', [])[-3:])}

BEST RESULT SO FAR:
{_serialize_for_prompt(context.get('best_result'))}

=== YOUR ANALYSIS ===

Provide your analysis in the following JSON format:
{{
    "plan_achieved": true/false,
    "plan_achievement_reasoning": "Why the plan was/wasn't achieved",
    "ideal_state_progress": "improving/stagnating/regressing",
    "ideal_state_reasoning": "Assessment of progress toward ideal state",
    "what_worked": ["List of things that worked well"],
    "what_failed": ["List of things that failed or need improvement"],
    "root_cause": "The primary root cause if there was a failure",
    "adjustments": ["Specific actionable adjustments for next iteration"],
    "insights": ["Key insights and learnings from this iteration"]
}}

Be SPECIFIC and ACTIONABLE. Focus on root causes, not symptoms.
If there was a build/test failure, identify it clearly.
If we're making progress, acknowledge what's working."""

    try:
        response = await llm_client.generate(
            prompt=prompt,
            system_prompt=(
                "You are an expert at analyzing software development iterations. "
                "Assess whether the plan was achieved, whether we're progressing toward "
                "the ideal state, and extract actionable lessons. Be specific and honest. "
                "Return valid JSON."
            ),
        )

        # Try to parse JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            lessons = json.loads(json_match.group())
            # Ensure required fields exist
            lessons.setdefault("plan_achieved", False)
            lessons.setdefault("ideal_state_progress", "unknown")
            lessons.setdefault("what_worked", [])
            lessons.setdefault("what_failed", [])
            lessons.setdefault("adjustments", [])
            lessons.setdefault("insights", [])
            return lessons
        else:
            # Fallback if JSON parsing fails
            logger.warning("Could not parse JSON from LLM response, using fallback")
            return {
                "plan_achieved": False,
                "plan_achievement_reasoning": "Could not parse LLM analysis",
                "ideal_state_progress": "unknown",
                "what_worked": [],
                "what_failed": ["LLM analysis parsing failed"],
                "adjustments": [],
                "insights": [response[:500]],  # Include raw response as insight
            }

    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}")
        # Return minimal lessons on error
        return {
            "plan_achieved": False,
            "plan_achievement_reasoning": f"Analysis error: {e}",
            "ideal_state_progress": "unknown",
            "what_worked": [],
            "what_failed": [f"LLM analysis error: {str(e)}"],
            "adjustments": ["Retry analysis next iteration"],
            "insights": [],
        }
