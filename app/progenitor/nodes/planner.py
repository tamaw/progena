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


def _get_target_capabilities(state: ProgenitorState) -> list[str]:
    """Safely extract target_capabilities from state's current_decision."""
    decision = state.get("current_decision")
    if decision and hasattr(decision, "target_capabilities"):
        return decision.target_capabilities or []
    return []


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
        if not decision:
            if span:
                span.set_attribute("skipped", True)
                span.end()
            return {}

        # Get decision action - Decision is always a Pydantic model
        action = decision.action
        action_value = action.value if hasattr(action, 'value') else str(action)

        # Handle system tool requests directly without LLM planning
        if action_value == "system_tool":
            target_caps = decision.target_capabilities or []
            tool_name = target_caps[0] if target_caps else "unknown"

            work_item = WorkItem(
                id=str(uuid.uuid4()),
                item_type=WorkItemType.SYSTEM_TOOL,
                status=WorkItemStatus.PENDING,
                description=f"Execute system tool: {tool_name}",
                target_tool=tool_name,
                context={"tool_name": tool_name},
            )
            backlog = list(state.get("backlog", []))
            backlog.append(work_item)

            if span:
                span.set_attribute("plan_type", "system_tool")
                span.set_attribute("work_items_created", 1)
                span.end()

            return {"backlog": backlog}

        # Handle schedule requests - use LLM to generate instruction
        if action_value == "schedule" and user_input:
            schedule_result = await _plan_schedule_task(
                user_input=user_input,
                decision=decision,
                llm_client=llm_client,
                state_manager=state_manager,
            )

            if schedule_result:
                # Return schedule entry to be added to state
                if span:
                    span.set_attribute("plan_type", "schedule")
                    span.set_attribute("work_items_created", 0)
                    span.end()
                # Cast to ProgenitorState since we're returning a partial state dict
                return schedule_result  # type: ignore[return-value]

            if span:
                span.set_attribute("plan_type", "schedule_failed")
                span.end()
            return {}

        if not decision.requires_planning:
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


async def _plan_schedule_task(
    user_input: str,
    decision,
    llm_client,
    state_manager,
) -> dict | None:
    """
    Plan a scheduled task by using the LLM to generate an instruction.

    The instruction is a processed version of the user's request that will
    be executed at the scheduled time as if it were a new user input.
    """
    from ..tools.schedule import add_scheduled_task
    from ..state.models import ScheduleEntry

    # Get schedule params from decision
    if hasattr(decision, "model_dump"):
        decision_dict = decision.model_dump()
    elif isinstance(decision, dict):
        decision_dict = decision
    else:
        decision_dict = {}

    schedule_params = decision_dict.get("schedule_params", {})
    time_expression = schedule_params.get("time_expression", "")

    if not time_expression:
        logger.warning("No time expression found for schedule request")
        return None

    try:
        # Use LLM to generate the instruction that will run at scheduled time
        instruction_prompt = f"""The user wants to schedule a task. Convert their request into an instruction that will be executed at the scheduled time.

User request: "{user_input}"
Scheduled time: {time_expression}

Generate a clear instruction that:
1. Captures what the user wants done at that time
2. Is written as if it's a new request to an AI assistant
3. Includes any context needed to fulfill the request

Example:
- User: "say hello world to me at 9am"
- Instruction: "Say 'Hello World!' to the user. This is a scheduled greeting they requested."

Another example:
- User: "check the weather every morning at 8am"
- Instruction: "Check and report the current weather conditions for the user's location."

Generate ONLY the instruction, nothing else:"""

        instruction = await llm_client.complete(
            prompt=instruction_prompt,
            system_prompt="You generate clear instructions for scheduled tasks. Output only the instruction text.",
        )

        instruction = instruction.strip()
        if not instruction:
            instruction = f"Execute the user's scheduled request: {user_input}"

        logger.info(f"Generated schedule instruction: {instruction[:100]}...")

        # Create the schedule entry
        result, entry_data = await add_scheduled_task(
            user_request=user_input,
            time_expression=time_expression,
            instruction=instruction,
            description=f"Scheduled: {user_input[:50]}...",
            recurring="every" in time_expression.lower(),
        )

        if result.success and entry_data:
            # Log the schedule creation
            await state_manager.log_decision({
                "event": "schedule_created",
                "schedule_id": result.schedule_id,
                "instruction": instruction,
                "time_expression": time_expression,
                "timestamp": utcnow_isoformat(),
            })

            # Create a ScheduleEntry from the entry_data
            entry = ScheduleEntry(**entry_data)

            # Return as "new_schedule_entry" - the commit node will merge it
            return {
                "new_schedule_entry": entry,
                "pending_response": f"✅ Scheduled: {result.message}\nNext run: {result.next_run}",
            }
        else:
            logger.warning(f"Failed to create schedule: {result.message}")
            return {
                "pending_response": f"❌ Could not schedule task: {result.message}",
            }

    except Exception as e:
        logger.error(f"Error planning schedule task: {e}", exc_info=True)
        return {
            "pending_response": f"❌ Error scheduling task: {e}",
        }


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
    elif decision_action == "system_tool":
        item_type = WorkItemType.SYSTEM_TOOL
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
                "capabilities": _get_target_capabilities(state),
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
                "capabilities": _get_target_capabilities(state),
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
