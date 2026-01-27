"""
Planner Node - Generates work items for complex tasks.
"""

import logging
import time
from datetime import datetime
from typing import Callable, Coroutine, Any
import uuid

from ..state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    DecisionAction,
)
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


def create_node(llm_client, state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the planner node function."""

    async def planner_node(state: ProgenitorState) -> ProgenitorState:
        """Generate work items based on decision or ideal state (loop.spec.md PLAN phase)."""
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        decision = state.get("current_decision")
        user_input = state.get("current_input")
        ideal_state = state.get("ideal_state")
        current_approaches = state.get("current_approaches", [])

        if tracer:
            span = tracer.start_span(
                "node_planner",
                attributes={
                    "has_decision": decision is not None,
                    "has_ideal_state": ideal_state is not None,
                    "approaches_count": len(current_approaches),
                },
            )

        # If in inner loop with ideal state, plan based on THINK phase approaches
        if ideal_state and current_approaches:
            result = await _plan_from_ideal_state(
                state, ideal_state, current_approaches, llm_client, state_manager
            )
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "planner"})
                metrics.increment("node_invocations_total", {"node": "planner", "success": "true"})
            if span:
                span.set_attribute("plan_type", "ideal_state")
                span.set_attribute("duration_seconds", duration)
                span.end()
            return result

        # Otherwise, use traditional planning from decision
        if not decision or not decision.requires_planning:
            if span:
                span.set_attribute("skipped", True)
                span.end()
            return {}

        # Get current backlog for context
        backlog = list(state.get("backlog", []))
        backlog_dicts = []
        for item in backlog:
            if hasattr(item, "model_dump"):
                backlog_dicts.append(item.model_dump())
            elif isinstance(item, dict):
                backlog_dicts.append(item)

        try:
            # Ask LLM to plan work items
            decision_dict = decision.model_dump() if hasattr(decision, "model_dump") else decision

            work_items_data = await llm_client.plan(
                user_input=user_input,
                decision=decision_dict,
                current_backlog=backlog_dicts,
            )

            # Convert to WorkItem objects
            for item_data in work_items_data:
                item_type_str = item_data.get("item_type", "direct_answer")
                try:
                    item_type = WorkItemType(item_type_str)
                except ValueError:
                    item_type = WorkItemType.DIRECT_ANSWER

                work_item = WorkItem(
                    id=item_data.get("id", str(uuid.uuid4())),
                    item_type=item_type,
                    status=WorkItemStatus.PENDING,
                    description=item_data.get("description", ""),
                    target_agent=item_data.get("target_agent"),
                    target_tool=item_data.get("target_tool"),
                    context=item_data.get("context", {}),
                )
                backlog.append(work_item)

            # Log planning
            await state_manager.log_decision({
                "event": "planning",
                "decision": decision_dict,
                "work_items_created": len(work_items_data),
                "timestamp": utcnow_isoformat(),
            })

            # Record telemetry
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "planner"})
                metrics.increment("node_invocations_total", {"node": "planner", "success": "true"})
                metrics.record_histogram("planner_work_items_created", len(work_items_data))

            if span:
                span.set_attribute("work_items_created", len(work_items_data))
                span.set_attribute("duration_seconds", duration)
                span.add_event("planning_complete", {"work_items": len(work_items_data)})
                span.end()

            return {"backlog": backlog}

        except Exception as e:
            # Log error but continue
            if metrics:
                metrics.increment("node_invocations_total", {"node": "planner", "success": "false"})
            if span:
                span.record_exception(e)
                span.end()
            await state_manager.log_decision({
                "event": "planning_error",
                "error": str(e),
                "timestamp": utcnow_isoformat(),
            })
            return {}

    return planner_node


async def _plan_from_ideal_state(
    state: ProgenitorState,
    ideal_state,
    approaches: list[dict],
    llm_client,
    state_manager,
) -> ProgenitorState:
    """
    PLAN phase for loop.spec.md inner loop.

    Choose experiment from THINK approaches and define expected measurable outcome.
    Per spec: "PLAN – choose experiment and expected measurable outcome"
    """
    iteration = state.get("current_iteration", 0)
    backlog = list(state.get("backlog", []))
    last_verification = state.get("last_verification_report")

    # Get decision to determine work item type
    decision = state.get("current_decision")
    decision_action = None
    if decision:
        if hasattr(decision, "action"):
            action = decision.action
            decision_action = action.value if hasattr(action, 'value') else str(action)
        elif isinstance(decision, dict):
            action = decision.get("action", "answer_directly")
            decision_action = action.value if hasattr(action, 'value') else str(action)

    # Determine work item type based on decision action
    if decision_action == "build_agent":
        item_type = WorkItemType.BUILD_AGENT
    elif decision_action == "build_mcp":
        item_type = WorkItemType.BUILD_MCP
    elif decision_action == "upgrade":
        item_type = WorkItemType.UPGRADE
    elif decision_action in ("call_agent", "delegate"):
        item_type = WorkItemType.A2A_CALL
    elif decision_action == "call_tool":
        item_type = WorkItemType.MCP_CALL
    else:
        item_type = WorkItemType.DIRECT_ANSWER

    # Select best approach based on gaps (if any) or use first approach
    chosen_approach = _select_best_approach(approaches, last_verification)

    # Convert ideal state to dict for LLM
    ideal_state_dict = ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state

    # Extract key context for planning
    task_description = ideal_state_dict.get("task_description", "")
    success_criteria = ideal_state_dict.get("success_criteria", [])

    # Handle last_verification as either dict or VerificationReport
    if last_verification:
        if hasattr(last_verification, "model_dump"):
            verif_dict = last_verification.model_dump()
        elif isinstance(last_verification, dict):
            verif_dict = last_verification
        else:
            verif_dict = {"gaps": []}
        gaps = verif_dict.get("gaps", [])
    else:
        gaps = []

    try:
        # Generate experiment with expected measurable outcome
        prompt = f"""Define an experiment to test this approach:

APPROACH: {chosen_approach.get('name')}
{chosen_approach.get('description', '')}

GOAL: {task_description}

SUCCESS CRITERIA TO TARGET:
{chr(10).join([f"- {c.get('description', '')} (target: {c.get('target_score', 0.8)})" for c in success_criteria[:3]])}

{"GAPS TO ADDRESS:" + chr(10) + chr(10).join([f"- {gap}" for gap in gaps[:3]]) if gaps else ""}

Define:
1. EXPERIMENT: What specific action/change to make
2. EXPECTED MEASURABLE OUTCOME: How we'll know it worked (specific, measurable)
3. VERIFICATION METHOD: How to verify success"""

        response = await llm_client.generate(
            prompt=prompt,
            system_prompt="Define experiments with clear measurable outcomes. Be specific.",
        )

        # Parse experiment details
        experiment_details = _parse_experiment(response)

        # Create work item with experiment context and correct type
        work_item = WorkItem(
            id=str(uuid.uuid4()),
            item_type=item_type,
            status=WorkItemStatus.PENDING,
            description=f"Experiment: {experiment_details.get('name', chosen_approach.get('name', 'approach'))}",
            context={
                "iteration": iteration,
                "approach": chosen_approach,
                "experiment": experiment_details.get("experiment", response),
                "expected_outcome": experiment_details.get("expected_outcome", "Verification passes"),
                "verification_method": experiment_details.get("verification_method", "Score against criteria"),
                "targeted_gaps": gaps[:3] if gaps else [],
                "plan_output": response,
                "capabilities": state.get("current_decision", {}).get("target_capabilities", []) if isinstance(state.get("current_decision"), dict) else [],
            },
        )
        backlog.append(work_item)
        logger.info(f"PLANNER: Created work item id={work_item.id}, type={item_type}, status={work_item.status}")
        logger.info(f"PLANNER: Backlog now has {len(backlog)} items")

        # Log planning with experiment details
        await state_manager.log_decision({
            "event": "plan_from_ideal_state",
            "iteration": iteration,
            "approach": chosen_approach.get("name"),
            "experiment": experiment_details.get("experiment", "")[:200],
            "expected_outcome": experiment_details.get("expected_outcome", "")[:200],
            "work_items_created": 1,
            "timestamp": utcnow_isoformat(),
        })

        return {
            "backlog": backlog,
            "current_experiment": experiment_details,
        }

    except Exception as e:
        # Fallback - create simple experiment work item with correct type
        work_item = WorkItem(
            id=str(uuid.uuid4()),
            item_type=item_type,
            status=WorkItemStatus.PENDING,
            description=f"Execute approach: {chosen_approach.get('name', 'default')}",
            context={
                "iteration": iteration,
                "approach": chosen_approach,
                "experiment": "Execute selected approach",
                "expected_outcome": "Verification score improves",
                "capabilities": state.get("current_decision", {}).get("target_capabilities", []) if isinstance(state.get("current_decision"), dict) else [],
            },
        )
        backlog.append(work_item)

        await state_manager.log_decision({
            "event": "plan_fallback",
            "error": str(e),
            "timestamp": utcnow_isoformat(),
        })

        return {"backlog": backlog}


def _select_best_approach(approaches: list[dict], last_verification) -> dict:
    """Select best approach based on gaps and verification results."""
    if not approaches:
        return {"name": "default", "description": "Execute based on current decision"}

    if not last_verification:
        return approaches[0]

    # Handle last_verification as either dict or VerificationReport
    if hasattr(last_verification, "model_dump"):
        verif_dict = last_verification.model_dump()
    elif isinstance(last_verification, dict):
        verif_dict = last_verification
    else:
        return approaches[0]

    # If there are gaps, prefer approaches that address them
    gaps = verif_dict.get("gaps", [])
    if gaps:
        for approach in approaches:
            desc = approach.get("description", "").lower()
            # Simple heuristic: check if approach mentions gap keywords
            for gap in gaps:
                if any(word in desc for word in gap.lower().split()[:3]):
                    return approach

    return approaches[0]


def _parse_experiment(response: str) -> dict:
    """Parse experiment details from LLM response."""
    result = {
        "name": "experiment",
        "experiment": "",
        "expected_outcome": "",
        "verification_method": "",
    }

    lines = response.split("\n")
    current_section = None

    for line in lines:
        line_lower = line.lower().strip()

        if "experiment:" in line_lower:
            current_section = "experiment"
            result["experiment"] = line.split(":", 1)[-1].strip()
        elif "expected" in line_lower and "outcome" in line_lower:
            current_section = "expected_outcome"
            result["expected_outcome"] = line.split(":", 1)[-1].strip()
        elif "verification" in line_lower:
            current_section = "verification_method"
            result["verification_method"] = line.split(":", 1)[-1].strip()
        elif current_section and line.strip() and not line.strip().startswith(("#", "-", "1", "2", "3")):
            result[current_section] += " " + line.strip()

    return result
