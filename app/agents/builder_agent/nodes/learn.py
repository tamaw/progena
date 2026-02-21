"""
LEARN Node - Learning phase of inner loop (loop.spec.md).

Analyzes whether the plan was achieved and whether we're progressing
toward the ideal state. Feeds all context to the LLM for holistic assessment.

Uses structured LLM output (LearningResult) for validated responses,
PromptTemplates for maintainable prompts, and local pattern detection
as a fallback for cross-iteration analysis.
"""

import json
import logging
from typing import Any, cast

from ..llm.prompts import PromptTemplates
from ..llm.response_models import LearningResult
from ..runtime.time_utils import utcnow_isoformat
from ..state.helpers import get_ideal_state
from ..state.models import ProgenitorState
from .types import NodeFunction, NodeStateUpdate

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


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
    context: dict[str, Any] = {}

    # Iteration info
    context["iteration"] = state.get("iteration", {}).get("current", 0)
    context["user_request"] = (state.get("build_settings") or {}).get("user_request", "")

    # Ideal state - the target we're trying to reach
    ideal_state = get_ideal_state(state)
    if ideal_state:
        if isinstance(ideal_state, dict):
            context["ideal_state"] = ideal_state
        elif hasattr(ideal_state, "model_dump"):
            context["ideal_state"] = ideal_state.model_dump()
        else:
            context["ideal_state"] = ideal_state

    # Experiment plan - what we planned to do this iteration
    phases = state.get("phases", {})
    plan_phase = phases.get("plan", {})
    experiment_plan = plan_phase.get("experiment_plan")
    if experiment_plan:
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
    verification_report = state.get("verification", {}).get("current_report")
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
        context["verification_report"] = verification_report

    # History - previous iterations (full list for pattern detection)
    history = state.get("history", {})
    context["previous_lessons"] = history.get("lessons_learned", [])
    context["previous_artefacts"] = history.get("artefacts", [])

    # Best result so far
    best = state.get("best")
    if best:
        context["best_result"] = best

    return context


def _detect_local_patterns(all_lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect patterns from lesson history (non-LLM fallback).

    Scans accumulated lessons for recurring failures and stagnation signals
    that may not be visible in a single-iteration LLM analysis.
    """
    patterns: list[dict[str, Any]] = []
    if len(all_lessons) < 2:
        return patterns

    # Check for recurring failures across iterations
    failure_counts: dict[str, list[int]] = {}
    for lesson in all_lessons:
        for failure in lesson.get("what_failed", []):
            # Normalize to detect similar failures
            failure_key = failure[:80].lower().strip()
            failure_counts.setdefault(failure_key, []).append(
                lesson.get("iteration", 0)
            )

    for failure, iterations in failure_counts.items():
        if len(iterations) >= 2:
            patterns.append({
                "pattern_type": "recurring_issue",
                "description": f"Recurring failure: {failure}",
                "affected_iterations": iterations,
                "recommended_action": "Address root cause rather than symptoms",
            })

    # Check for stagnation (same progress status across recent iterations)
    recent = all_lessons[-3:]
    if len(recent) >= 3:
        progress_values = [lesson_item.get("ideal_state_progress", "unknown") for lesson_item in recent]
        if all(p == "stagnating" for p in progress_values):
            patterns.append({
                "pattern_type": "stagnation_signal",
                "description": "Progress stagnating for 3+ iterations",
                "affected_iterations": [lesson_item.get("iteration", 0) for lesson_item in recent],
                "recommended_action": "Consider fundamentally different approach",
            })

    # Check for regression pattern
    if len(recent) >= 2:
        progress_values = [lesson_item.get("ideal_state_progress", "unknown") for lesson_item in recent]
        if all(p == "regressing" for p in progress_values):
            patterns.append({
                "pattern_type": "regression_pattern",
                "description": "Regression detected across recent iterations",
                "affected_iterations": [lesson_item.get("iteration", 0) for lesson_item in recent],
                "recommended_action": "Revert to best known approach and investigate cause",
            })

    return patterns


def _get_response_format(llm_client) -> dict[str, Any]:
    """Get structured output response format for LearningResult."""
    if hasattr(llm_client, '_get_json_response_format'):
        return cast(dict[str, Any], llm_client._get_json_response_format(LearningResult, "learning_result"))
    # Fallback: import the builder from the client module
    from ..llm.client import _build_json_schema_format
    return _build_json_schema_format(LearningResult, "learning_result")


def create_node(
    llm_client,
    state_manager=None,
) -> NodeFunction:
    """Create the learn node function.

    CQ-006: Returns NodeFunction type - async function that takes
    ProgenitorState and returns NodeStateUpdate (partial dict).
    """

    prompts = PromptTemplates()

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

        iteration = state.get("iteration", {}).get("current", 0)

        # Gather ALL context for LLM analysis
        context = _gather_full_context(state)

        # Generate lessons via structured LLM call
        lessons_result = await _generate_lessons_via_llm(llm_client, context, prompts)

        # Convert to dict for state storage
        lessons = lessons_result.model_dump()
        lessons["iteration"] = iteration

        # Merge local pattern detection with LLM-detected patterns
        history = state.get("history", {})
        all_lessons = list(history.get("lessons_learned", []))

        local_patterns = _detect_local_patterns(all_lessons)
        if local_patterns:
            # Merge: add local patterns not already detected by LLM
            llm_pattern_descs = {
                p.get("description", "").lower()
                for p in lessons.get("patterns_detected", [])
            }
            for pattern in local_patterns:
                if pattern["description"].lower() not in llm_pattern_descs:
                    lessons.setdefault("patterns_detected", []).append(pattern)

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
                "patterns_count": len(lessons.get("patterns_detected", [])),
                "timestamp": utcnow_isoformat(),
            })

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

        # Build learning summary for observe node consumption (Phase 5)
        learning_summary = {
            "iteration": iteration,
            "plan_achieved": lessons.get("plan_achieved", False),
            "ideal_state_progress": lessons.get("ideal_state_progress", "unknown"),
            "what_worked": lessons.get("what_worked", []),
            "what_failed": lessons.get("what_failed", []),
            "adjustments": lessons.get("adjustments", []),
            "insights": lessons.get("insights", []),
            "root_cause": lessons.get("root_cause", ""),
            "patterns_detected": lessons.get("patterns_detected", []),
            "prioritized_adjustments": lessons.get("prioritized_adjustments", []),
            "action_outcome_correlations": lessons.get("action_outcome_correlations", []),
        }

        return {
            "history": full_history,
            "build_phase": "learn",
            "learning_summary": learning_summary,
        }

    return learn_node


async def _generate_lessons_via_llm(
    llm_client,
    context: dict[str, Any],
    prompts: PromptTemplates,
) -> LearningResult:
    """Use LLM to analyze all context and generate comprehensive lessons.

    Uses structured output (LearningResult) for validated responses and
    PromptTemplates for maintainable prompts. Falls back gracefully on error.
    """

    # Build prompt from template
    prompt = prompts.get_learn_prompt(
        iteration=context.get("iteration", 0),
        user_request=context.get("user_request", "Not provided"),
        ideal_state=_serialize_for_prompt(context.get("ideal_state")),
        experiment_plan=_serialize_for_prompt(context.get("experiment_plan")),
        think_phase=_serialize_for_prompt(context.get("think_phase")),
        execution_result=_serialize_for_prompt(context.get("execution_result")),
        verification_report=_serialize_for_prompt(context.get("verification_report")),
        previous_lessons=_serialize_for_prompt(context.get("previous_lessons", [])[-3:]),
        best_result=_serialize_for_prompt(context.get("best_result")),
    )

    system_prompt = _prompts.get("builder_agent/learn_system.md")

    try:
        if hasattr(llm_client, "chat_structured"):
            return cast(LearningResult, await llm_client.chat_structured(
                messages=[{"role": "user", "content": prompt}],
                response_model=LearningResult,
                schema_name="learning_result",
                system_prompt=system_prompt,
                temperature=0.3,
            ))

        # Fallback for test doubles / alternate clients
        response_format = _get_response_format(llm_client)

        if response_format.get("type") == "json_object":
            system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            temperature=0.3,
            response_format=response_format,
        )

        raw_data = json.loads(response)
        return LearningResult.model_validate(raw_data)

    except json.JSONDecodeError:
        logger.warning("Could not parse JSON from LLM response, using fallback")
        return LearningResult(
            plan_achieved=False,
            plan_achievement_reasoning="Could not parse LLM analysis",
            ideal_state_progress="unknown",
            what_failed=["LLM analysis parsing failed"],
        )

    except Exception as e:
        logger.warning(f"LLM analysis failed: {e}")
        return LearningResult(
            plan_achieved=False,
            plan_achievement_reasoning=f"Analysis error: {e}",
            ideal_state_progress="unknown",
            what_failed=[f"LLM analysis error: {str(e)}"],
            adjustments=["Retry analysis next iteration"],
        )
