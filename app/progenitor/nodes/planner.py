"""
Planner Node - Generates work items for complex tasks.

BL-002: Extended to emit experiment_plan - a structured plan with prompts,
expected response shape, scoring hooks, number of trials, and stop-early rules.

Spec Artifact: PLAN owns a persistent "Spec" Markdown artifact stored in
build_state.json that evolves each iteration and is consumed by BUILD.
"""

import logging
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Any
import uuid

from ..state.models import (
    ProgenitorState,
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    DecisionAction,
)
from ..state.execution_schemas import (
    ExperimentPlan,
    PromptSpec,
    ScoringHook,
    StopEarlyRule,
    SpecDoc,
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
        # Read approaches from nested structure (phases.think.approaches)
        current_approaches = state.get("phases", {}).get("think", {}).get("approaches", [])

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

        # NOTE: Build actions (build_agent, build_mcp, upgrade) are handled by
        # the inner loop graph via BackgroundBuildRunner, not by this planner.
        # The decide node starts builds directly and returns ANSWER_DIRECTLY.

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

            schedule_result_payload = {
                "description": entry_data.get("description"),
                "instruction": instruction,
                "recurring": bool(entry_data.get("repeat_minutes")),
                "next_run": result.next_run,
                "schedule_id": result.schedule_id,
            }

            try:
                pending_response = await llm_client.complete(
                    prompt=(
                        "Create a short, friendly confirmation for a scheduled task. "
                        "Do not include IDs or exact timestamps. Do not use markdown. "
                        "Focus on what will happen.\n\n"
                        f"Task details: {json.dumps(schedule_result_payload, ensure_ascii=False)}"
                    ),
                    system_prompt="You write concise confirmations for scheduled tasks.",
                    temperature=0.2,
                )
                pending_response = pending_response.strip() if pending_response else None
            except Exception:
                pending_response = None

            if not pending_response:
                pending_response = f"Got it. I’ll {instruction}."

            # Return as "new_schedule_entry" - the commit node will merge it
            return {
                "new_schedule_entry": entry,
                "pending_response": pending_response,
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


# NOTE: _plan_build_task removed - builds now go through inner loop graph
# via BackgroundBuildRunner, not via backlog work items.


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

    Also manages the persistent SpecDoc artifact:
    - First run (no spec present): Seeds from canonical template
    - Subsequent iterations: Updates spec based on THINK outputs
    """
    iteration = state.get("current_iteration", 0)
    backlog = list(state.get("backlog", []))
    last_verification = state.get("last_verification_report")

    # Determine work item type from multiple sources (priority order):
    # 1. Current work item being processed (most reliable)
    # 2. Existing backlog items with build types
    # 3. Current decision action
    # 4. Default to direct_answer
    item_type = WorkItemType.DIRECT_ANSWER  # default

    # Check current work item first (set by pick_next)
    current_item = state.get("current_work_item")
    if current_item:
        if hasattr(current_item, "item_type"):
            item_type = current_item.item_type
        elif isinstance(current_item, dict) and "item_type" in current_item:
            item_type_str = current_item["item_type"]
            try:
                item_type = WorkItemType(item_type_str)
            except ValueError:
                pass
        logger.debug(f"PLANNER: Got item_type={item_type} from current_work_item")

    # If not found, check backlog for build items
    if item_type == WorkItemType.DIRECT_ANSWER and backlog:
        for item in backlog:
            if hasattr(item, "item_type"):
                if item.item_type in (WorkItemType.BUILD_AGENT, WorkItemType.BUILD_MCP, WorkItemType.UPGRADE):
                    item_type = item.item_type
                    logger.debug(f"PLANNER: Got item_type={item_type} from backlog item")
                    break
            elif isinstance(item, dict) and "item_type" in item:
                item_type_str = item["item_type"]
                if item_type_str in ("build_agent", "build_mcp", "upgrade"):
                    try:
                        item_type = WorkItemType(item_type_str)
                        logger.debug(f"PLANNER: Got item_type={item_type} from backlog dict")
                        break
                    except ValueError:
                        pass

    # Fall back to decision if still direct_answer
    if item_type == WorkItemType.DIRECT_ANSWER:
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

    logger.info(f"PLANNER: Determined item_type={item_type} for inner loop work item")

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

    # Get agent name from multiple sources (priority order):
    # 1. build_settings (BL-007: Most authoritative source)
    # 2. Current work item context
    # 3. Decision target_capabilities
    # 4. Extract from task description
    # 5. Default to unknown_agent
    agent_name = "unknown_agent"
    project_directory = None  # BL-007: Will be set from build_settings if available

    # BL-007: Check build_settings first (most reliable source)
    build_settings = state.get("build_settings")
    if build_settings:
        if isinstance(build_settings, dict):
            agent_name = build_settings.get("project_name") or agent_name
            project_directory = build_settings.get("project_directory")
        elif hasattr(build_settings, "project_name"):
            agent_name = build_settings.project_name or agent_name
            project_directory = getattr(build_settings, "project_directory", None)
        logger.debug(f"PLANNER: Got agent_name={agent_name}, project_directory={project_directory} from build_settings")

    # Try current work item context if not found
    if agent_name == "unknown_agent" and current_item:
        if hasattr(current_item, "context") and current_item.context:
            ctx = current_item.context
            if isinstance(ctx, dict):
                # Check for agent_name or target_capabilities in context
                agent_name = ctx.get("agent_name") or agent_name
                if agent_name == "unknown_agent":
                    caps = ctx.get("target_capabilities", [])
                    if caps and isinstance(caps, list) and caps[0]:
                        agent_name = caps[0]
        elif isinstance(current_item, dict):
            ctx = current_item.get("context", {})
            if isinstance(ctx, dict):
                agent_name = ctx.get("agent_name") or agent_name
                if agent_name == "unknown_agent":
                    caps = ctx.get("target_capabilities", [])
                    if caps and isinstance(caps, list) and caps[0]:
                        agent_name = caps[0]

    # Try decision target_capabilities
    if agent_name == "unknown_agent":
        decision = state.get("current_decision")
        if decision:
            target_caps = _get_target_capabilities(state)
            if target_caps:
                agent_name = target_caps[0]

    # Try to extract from task description (e.g., "build a weather agent")
    if agent_name == "unknown_agent" and task_description:
        import re
        # Look for patterns like "weather agent", "hello world agent"
        match = re.search(r'(?:build\s+(?:a\s+|an\s+|me\s+a\s+)?)?(\w+(?:\s+\w+)?)\s+agent', task_description.lower())
        if match:
            extracted = match.group(1).strip()
            if extracted and extracted not in ("a", "an", "the", "new", "build", "me"):
                agent_name = extracted.replace(" ", "_") + "_agent"

    logger.info(f"PLANNER: Determined agent_name={agent_name} for spec doc")

    # Generate work item ID early for spec file naming
    work_item_id = str(uuid.uuid4())

    # BL-BUG-002: Get lessons_learned from state for spec_doc update
    lessons_learned = state.get("lessons_learned", [])

    # =========================================================================
    # SpecDoc Management: Seed or Update the persistent spec artifact
    # BL-007: Pass project_directory from build_settings for consistent spec location
    # =========================================================================
    spec_doc = await _ensure_spec_doc(
        state_manager=state_manager,
        agent_name=agent_name,
        task_description=task_description,
        approaches=approaches,
        gaps=gaps,
        iteration=iteration,
        llm_client=llm_client,
        work_item_id=work_item_id,
        project_directory=project_directory,  # BL-007
        lessons_learned=lessons_learned,  # BL-BUG-002
    )

    # Store spec_doc in state for BUILD to consume
    if hasattr(spec_doc, "model_dump"):
        spec_doc_dict = spec_doc.model_dump()
    elif isinstance(spec_doc, dict):
        spec_doc_dict = spec_doc
    else:
        spec_doc_dict = {"content": str(spec_doc)}

    try:
        # BL-002: Generate structured ExperimentPlan
        experiment_plan = await _generate_experiment_plan(
            llm_client=llm_client,
            chosen_approach=chosen_approach,
            task_description=task_description,
            success_criteria=success_criteria,
            gaps=gaps,
            iteration=iteration,
            agent_name=agent_name,
        )

        # Get spec file path and copilot prompt
        spec_file_path = getattr(spec_doc, 'spec_file_path', None)
        copilot_prompt = getattr(spec_doc, 'copilot_prompt', None)

        # Create work item with experiment plan in context
        work_item = WorkItem(
            id=work_item_id,  # Use pre-generated ID for consistency with spec file
            item_type=item_type,
            status=WorkItemStatus.PENDING,
            description=f"Experiment: {chosen_approach.get('name', 'approach')}",
            context={
                "iteration": iteration,
                "approach": chosen_approach,
                "experiment_plan": experiment_plan.model_dump(),  # BL-002: Structured plan
                "expected_outcome": experiment_plan.expected_outcome,
                "verification_method": experiment_plan.verification_method,
                "targeted_gaps": gaps[:3] if gaps else [],
                "capabilities": _get_target_capabilities(state),
                "spec_doc": spec_doc_dict,  # SpecDoc: Include for BUILD consumption
                "spec_file_path": spec_file_path,  # Path to spec file in app/agents/
                "copilot_prompt": copilot_prompt,  # The prompt that references the spec file
            },
        )
        backlog.append(work_item)
        logger.info(f"PLANNER: Created work item id={work_item.id}, type={item_type}, status={work_item.status}")
        logger.info(f"PLANNER: Experiment plan has {len(experiment_plan.prompts)} prompts")
        logger.info(f"PLANNER: Backlog now has {len(backlog)} items")
        logger.info(f"PLANNER: SpecDoc revision={spec_doc.revision}, spec_file_path={spec_file_path}")
        logger.info(f"PLANNER: Copilot prompt generated ({len(copilot_prompt or '')} chars)")

        # Log planning with experiment plan details
        await state_manager.log_decision({
            "event": "plan_from_ideal_state",
            "iteration": iteration,
            "approach": chosen_approach.get("name"),
            "experiment_plan_id": experiment_plan.plan_id,
            "prompts_count": len(experiment_plan.prompts),
            "expected_outcome": experiment_plan.expected_outcome[:200],
            "spec_doc_revision": spec_doc.revision,
            "work_items_created": 1,
            "timestamp": utcnow_isoformat(),
        })

        return {
            # Nested structure
            "phases": {
                "plan": {
                    "experiment_plan": experiment_plan.model_dump(),
                },
            },
            "spec": {
                "file_path": spec_doc.spec_file_path if hasattr(spec_doc, 'spec_file_path') else None,
                "revision": spec_doc.revision,
                "created_at": str(spec_doc.created_at) if hasattr(spec_doc, 'created_at') else None,
                "updated_at": str(spec_doc.updated_at) if hasattr(spec_doc, 'updated_at') else None,
            },
            "backlog": backlog,
            "build_phase": "plan",
        }

    except Exception as e:
        logger.warning(f"Failed to generate experiment plan: {e}, using fallback")
        # Fallback - create smoke test plan
        experiment_plan = ExperimentPlan.create_smoke_test_plan(
            agent_name=agent_name,
            iteration=iteration,
        )

        # Get spec file path and copilot prompt from spec_doc (even in fallback)
        fallback_spec_file_path = getattr(spec_doc, 'spec_file_path', None)
        fallback_copilot_prompt = getattr(spec_doc, 'copilot_prompt', None)

        work_item = WorkItem(
            id=str(uuid.uuid4()),
            item_type=item_type,
            status=WorkItemStatus.PENDING,
            description=f"Execute approach: {chosen_approach.get('name', 'default')}",
            context={
                "iteration": iteration,
                "approach": chosen_approach,
                "experiment_plan": experiment_plan.model_dump(),
                "expected_outcome": "Verification score improves",
                "capabilities": _get_target_capabilities(state),
                "spec_doc": spec_doc_dict,  # SpecDoc: Include for BUILD consumption
                "spec_file_path": fallback_spec_file_path,  # Include spec file path
                "copilot_prompt": fallback_copilot_prompt,  # Include copilot prompt
            },
        )
        backlog.append(work_item)

        await state_manager.log_decision({
            "event": "plan_fallback",
            "error": str(e),
            "timestamp": utcnow_isoformat(),
        })

        return {
            # Nested structure (even in fallback)
            "phases": {
                "plan": {
                    "experiment_plan": experiment_plan.model_dump(),
                },
            },
            "backlog": backlog,
            "build_phase": "plan",
        }


async def _ensure_spec_doc(
    state_manager,
    agent_name: str,
    task_description: str,
    approaches: list[dict],
    gaps: list[str],
    iteration: int,
    llm_client=None,
    work_item_id: str | None = None,
    project_directory: str | None = None,  # BL-007: From build_settings
    lessons_learned: list[dict] | None = None,  # BL-BUG-002: From LEARN phase
) -> SpecDoc:
    """
    Ensure a SpecDoc exists for the current build task.

    BL-007: Now supports project_directory from build_settings for consistent spec file location.
    BL-BUG-002: Now includes lessons_learned from LEARN phase.

    - Spec file is dropped into project_directory/spec.md (or fallback to app/agents/<agent_name>/spec.md)
    - Copilot prompt references the spec file (not embedding content)
    - On first run: LLM generates BOTH spec content AND copilot prompt
    - Subsequent iterations: Updates spec and regenerates prompt

    Persists updates back to build_state.json and saves spec file.
    """
    # Try to load existing spec_doc from build_state
    existing_spec_doc_data = await state_manager.get_spec_doc()

    if existing_spec_doc_data:
        # Load existing spec and update it
        spec_doc = SpecDoc(**existing_spec_doc_data)

        # Update from current THINK outputs and lessons
        spec_doc.update_from_think(
            think_outputs=approaches,
            iteration=iteration,
            verification_gaps=gaps,
            lessons=lessons_learned,  # BL-BUG-002
        )

        logger.info(f"PLANNER: Updated SpecDoc to revision {spec_doc.revision}")
    else:
        # First run - use LLM to customize from canonical template AND generate prompt
        spec_doc = await _seed_spec_doc_from_template(
            agent_name=agent_name,
            task_description=task_description,
            llm_client=llm_client,
        )
        logger.info(f"PLANNER: Seeded new SpecDoc from template with LLM customization, revision {spec_doc.revision}")

    # Save spec file to appropriate directory for copilot to reference
    # BL-007: Use project_directory from build_settings if provided
    spec_file_path = await _save_spec_file(spec_doc, agent_name=agent_name, work_item_id=work_item_id, project_directory=project_directory)
    if spec_file_path:
        spec_doc.spec_file_path = str(spec_file_path)
        logger.info(f"PLANNER: Saved spec file to {spec_file_path}")

    # Generate the copilot prompt that references the spec file
    # This is the key change: prompt references file, doesn't embed content
    spec_doc.copilot_prompt = await _generate_copilot_prompt(
        spec_file_path=spec_file_path,
        agent_name=agent_name,
        task_description=task_description,
        iteration=iteration,
        gaps=gaps,
        llm_client=llm_client,
    )
    logger.info(f"PLANNER: Generated copilot prompt ({len(spec_doc.copilot_prompt or '')} chars)")

    # Persist updated spec_doc to build_state.json
    await state_manager.save_spec_doc(spec_doc)

    return spec_doc


async def _generate_copilot_prompt(
    spec_file_path: Path | None,
    agent_name: str,
    task_description: str,
    iteration: int,
    gaps: list[str],
    llm_client=None,
) -> str:
    """
    Generate the prompt that instructs Copilot to read and build from the spec file.

    The prompt:
    1. Tells Copilot where the spec file is
    2. Provides high-level context about the task
    3. On subsequent iterations, mentions what to fix/improve
    4. Detects if agent already exists and adjusts instructions accordingly
    """
    # Sanitize agent name for folder path
    safe_name = agent_name.replace(" ", "_").replace("-", "_").lower()

    # Calculate relative path for the prompt (from project root)
    if spec_file_path:
        # Convert to relative path from project root for cleaner prompt
        try:
            project_root = Path(__file__).parent.parent.parent.parent
            rel_path = spec_file_path.relative_to(project_root)
        except ValueError:
            rel_path = spec_file_path
    else:
        rel_path = f"app/agents/{safe_name}/spec.md"

    # Check if agent already exists
    project_root = Path(__file__).parent.parent.parent.parent
    agent_folder = project_root / "app" / "agents" / safe_name
    existing_agent = (agent_folder / "agent.py").exists()

    # Build context about what iteration we're on and what to focus on
    iteration_context = ""
    if iteration > 0 and gaps:
        iteration_context = f"""

This is iteration {iteration} of the build. Focus on addressing these gaps:
{chr(10).join(f'- {gap}' for gap in gaps[:5])}
"""

    # Adjust prompt based on whether agent exists
    if existing_agent:
        action_verb = "Update"
        action_details = f"""- This is an EXISTING agent - modify the existing files
- Review the current implementation in app/agents/{safe_name}/
- Update agent.py to match the new/updated spec requirements
- Preserve existing functionality unless the spec explicitly changes it"""
    else:
        action_verb = "Build"
        action_details = f"""- Create all required files in app/agents/{safe_name}/
- Follow the directory structure and file organization in the spec
- Implement all specified behaviors and responses"""

    # Base prompt that references the spec file
    base_prompt = f"""{action_verb} an AI agent based on the specification file.

SPEC FILE: {rel_path}

Read the spec file above and implement the agent according to its requirements.
{iteration_context}
IMPORTANT:
- Read the spec file FIRST to understand the full requirements
{action_details}"""

    # Optionally use LLM to enhance the prompt with task-specific guidance
    if llm_client and iteration == 0:
        try:
            enhancement_prompt = f"""Given this build task, generate a focused instruction for Copilot.

TASK: {task_description}
AGENT NAME: {agent_name}
SPEC FILE LOCATION: {rel_path}

Generate a concise, actionable prompt (2-4 sentences) that:
1. References the spec file for details
2. Emphasizes the key behavior/capability the agent should have
3. Mentions any critical implementation details

Output ONLY the prompt text, nothing else:"""

            enhanced_guidance = await llm_client.generate(
                prompt=enhancement_prompt,
                system_prompt="You generate focused build instructions. Be concise and actionable.",
            )

            if enhanced_guidance and len(enhanced_guidance.strip()) > 20:
                # Combine base structure with enhanced guidance
                return f"""{base_prompt}

TASK FOCUS:
{enhanced_guidance.strip()}"""

        except Exception as e:
            logger.warning(f"PLANNER: LLM prompt enhancement failed: {e}, using base prompt")

    return base_prompt


async def _save_spec_file(spec_doc: SpecDoc, agent_name: str | None = None, work_item_id: str | None = None, project_directory: str | None = None) -> Path | None:
    """
    Save the spec content to a file in the appropriate project directory for copilot CLI to reference.

    BL-007: Uses project_directory from build_settings if provided.

    The spec file is placed inside the agent's directory:
    - If project_directory provided: Creates {project_directory}/spec.md
    - Otherwise fallback: Creates app/agents/{agent_name}/spec.md

    Returns the path to the saved file.
    """
    try:
        # BL-007: Use project_directory from build_settings if available
        if project_directory:
            agent_folder = Path(project_directory)
            agent_folder.mkdir(parents=True, exist_ok=True)
            safe_name = agent_folder.name
            logger.info(f"PLANNER: Using project_directory from build_settings: {project_directory}")
        else:
            # Fallback: Determine project root and agents directory
            project_root = Path(__file__).parent.parent.parent.parent
            agents_dir = project_root / "app" / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)

            # Create agent folder based on agent name (preferred) or work_item_id
            if agent_name:
                # Sanitize agent name for folder name
                safe_name = agent_name.replace(" ", "_").replace("-", "_").lower()
            else:
                safe_name = f"agent_{work_item_id or 'unknown'}"

            # Create the agent folder
            agent_folder = agents_dir / safe_name
            agent_folder.mkdir(parents=True, exist_ok=True)

        # Check if agent already exists (has agent.py)
        existing_agent = (agent_folder / "agent.py").exists()
        if existing_agent:
            logger.info(f"PLANNER: Agent '{safe_name}' already exists, spec will amend existing agent")

        # Save spec.md inside the agent folder
        spec_file_path = agent_folder / "spec.md"
        spec_file_path.write_text(spec_doc.content, encoding="utf-8")

        logger.info(f"PLANNER: Saved spec file to {spec_file_path} (existing_agent={existing_agent})")
        return spec_file_path
    except Exception as e:
        logger.error(f"Failed to save spec file: {e}")
        return None


async def _seed_spec_doc_from_template(
    agent_name: str,
    task_description: str,
    llm_client=None,
) -> SpecDoc:
    """
    Seed a new SpecDoc by using LLM to customize the canonical template (agent_structure.md)
    with the user's specific task requirements.
    """
    # Determine path to seed template
    # Try project-relative path first
    seed_paths = [
        Path("data/prompts/agent_structure.md"),
        Path(__file__).parent.parent.parent.parent / "data/prompts/agent_structure.md",
    ]

    template_content = ""
    seed_path = "prompts/agent_structure.md"  # Default reference path

    for path in seed_paths:
        if path.exists():
            template_content = path.read_text(encoding="utf-8")
            seed_path = str(path.relative_to(path.parent.parent) if path.parent.parent.exists() else path.name)
            break

    if not template_content:
        # Fallback: Create a minimal spec if template not found
        template_content = """# Agent Structure Specification

## Directory Structure
Create the agent in `app/agents/{agent_name}/`:
- `app/agents/{agent_name}/__init__.py`
- `app/agents/{agent_name}/agent.py` - Main implementation

## Requirements
- Python best practices
- Docstrings and type hints
- Implement required A2A endpoints
"""
        seed_path = "generated/minimal_spec.md"
        logger.warning("SpecDoc: Template not found, using minimal fallback spec")

    # Use LLM to customize the template with user's task
    if llm_client:
        try:
            customization_prompt = f"""You are generating a build specification for an AI agent.

TASK FROM USER: {task_description}

AGENT NAME: {agent_name}

TEMPLATE TO CUSTOMIZE:
{template_content}

Generate a COMPLETE, DETAILED build specification that:
1. Replaces {{agent_name}} with "{agent_name}"
2. Replaces {{agent_class}} with "{agent_name.title().replace('_', '')}"
3. Adds specific implementation details based on the user's task
4. Includes the exact behavior the agent should have
5. Specifies what the agent's invoke() method should return for different inputs

For this task "{task_description}", specify:
- EXACTLY what the agent should respond with
- Any specific text/format requirements
- How it should handle various inputs

Output the complete customized specification markdown:"""

            customized_content = await llm_client.generate(
                prompt=customization_prompt,
                system_prompt="You are a technical specification writer. Generate detailed, actionable agent specifications. Be precise about expected behaviors and outputs.",
            )

            if customized_content and len(customized_content) > 100:
                logger.info(f"PLANNER: LLM customized spec ({len(customized_content)} chars)")
                seed_content = customized_content
            else:
                # Fallback to manual replacement
                logger.warning("PLANNER: LLM customization too short, using manual replacement")
                seed_content = template_content.replace("{agent_name}", agent_name)
                seed_content = seed_content.replace("{agent_class}", agent_name.title().replace("_", ""))
        except Exception as e:
            logger.error(f"PLANNER: LLM customization failed: {e}, using manual replacement")
            seed_content = template_content.replace("{agent_name}", agent_name)
            seed_content = seed_content.replace("{agent_class}", agent_name.title().replace("_", ""))
    else:
        # No LLM client - do manual replacement
        seed_content = template_content.replace("{agent_name}", agent_name)
        seed_content = seed_content.replace("{agent_class}", agent_name.title().replace("_", ""))

    # Add task-specific header
    header = f"""# Build Spec: {agent_name}

> Task: {task_description}
> Generated at: {utcnow_isoformat()}

---

"""

    return SpecDoc.create_from_seed(
        seed_content=header + seed_content,
        seed_path=seed_path,
        agent_name=agent_name,
    )


async def _generate_experiment_plan(
    llm_client,
    chosen_approach: dict,
    task_description: str,
    success_criteria: list[dict],
    gaps: list[str],
    iteration: int,
    agent_name: str,
) -> ExperimentPlan:
    """Generate a structured ExperimentPlan from approach and criteria.

    BL-002: Creates a structured plan with prompts, expected response shape,
    scoring hooks, number of trials, and stop-early rules.
    """
    # Build prompt for LLM to generate experiment details
    prompt = f"""Generate an experiment plan to test this approach:

APPROACH: {chosen_approach.get('name', 'primary')}
{chosen_approach.get('description', '')}

GOAL: {task_description}

SUCCESS CRITERIA:
{chr(10).join([f"- {c.get('description', '')} (target: {c.get('target_score', 0.8)})" for c in success_criteria[:3]])}

{"GAPS TO ADDRESS:" + chr(10) + chr(10).join([f"- {gap}" for gap in gaps[:3]]) if gaps else ""}

Generate:
1. 2-4 test prompts to send to the agent (first should be a smoke test)
2. Expected responses or patterns for each prompt
3. How to score responses (what to check for)
4. Expected measurable outcome

Format each prompt as:
PROMPT [n]: <the prompt text>
EXPECT: <what the response should contain or be like>
SCORE_BY: <how to score: contains "X", matches pattern, etc.>"""

    response = await llm_client.generate(
        prompt=prompt,
        system_prompt="Generate detailed experiment plans with specific test prompts. Be concrete and measurable.",
    )

    # Parse the response into structured PromptSpecs
    prompts = _parse_prompts_from_response(response)

    # Ensure we have at least a smoke test
    if not prompts:
        prompts = [
            PromptSpec(
                id="smoke_1",
                text="Hello, are you working?",
                is_smoke_test=True,
                expected_response_contains=["hello", "yes", "hi", "working"],
            )
        ]
    else:
        # Mark first prompt as smoke test
        prompts[0].is_smoke_test = True

    # Generate scoring hooks from success criteria
    scoring_hooks = _generate_scoring_hooks(success_criteria)

    # Generate stop-early rules
    stop_early_rules = [
        StopEarlyRule(
            id="consecutive_failures",
            rule_type="consecutive_failures",
            threshold=2,
            description="Stop if 2 consecutive prompts fail",
        ),
        StopEarlyRule(
            id="score_threshold",
            rule_type="score_threshold",
            threshold=0.9,
            description="Stop early if score exceeds 0.9",
        ),
    ]

    # Extract expected outcome from response
    expected_outcome = _extract_expected_outcome(response, chosen_approach)

    return ExperimentPlan(
        plan_id=f"exp_{uuid.uuid4().hex[:8]}",
        iteration=iteration,
        agent_name=agent_name,
        experiment_description=f"Test approach: {chosen_approach.get('name', 'primary')}",
        prompts=prompts,
        scoring_hooks=scoring_hooks,
        num_trials=1,
        stop_early_rules=stop_early_rules,
        expected_outcome=expected_outcome,
        verification_method="score_threshold",
    )


def _parse_prompts_from_response(response: str) -> list[PromptSpec]:
    """Parse PromptSpec objects from LLM response."""
    prompts = []
    lines = response.split("\n")

    current_prompt = None
    prompt_count = 0

    for line in lines:
        line_stripped = line.strip()
        line_lower = line_stripped.lower()

        # Look for PROMPT [n]: pattern
        if line_lower.startswith("prompt") and ":" in line_stripped:
            # Save previous prompt if exists
            if current_prompt:
                prompts.append(current_prompt)

            prompt_count += 1
            prompt_text = line_stripped.split(":", 1)[-1].strip()
            current_prompt = PromptSpec(
                id=f"prompt_{prompt_count}",
                text=prompt_text,
            )

        # Look for EXPECT: pattern
        elif current_prompt and line_lower.startswith("expect"):
            expect_text = line_stripped.split(":", 1)[-1].strip()
            # Extract keywords from expectation
            keywords = [w.strip().strip('"\'') for w in expect_text.split() if len(w) > 3]
            current_prompt.expected_response_contains = keywords[:5]

        # Look for SCORE_BY: pattern
        elif current_prompt and line_lower.startswith("score"):
            pass  # Could add custom scoring logic here

    # Don't forget the last prompt
    if current_prompt:
        prompts.append(current_prompt)

    return prompts


def _generate_scoring_hooks(success_criteria: list[dict]) -> list[ScoringHook]:
    """Generate scoring hooks from success criteria."""
    hooks = []

    for i, criterion in enumerate(success_criteria[:5]):
        criterion_desc = criterion.get("description", "").lower()

        # Determine hook type based on criterion description
        if "respond" in criterion_desc or "answer" in criterion_desc:
            hooks.append(ScoringHook(
                id=f"hook_{i+1}",
                name=f"Response check: {criterion.get('id', f'criterion_{i+1}')}",
                hook_type="contains",
                pattern=None,  # Will be filled based on specific responses
                weight=criterion.get("weight", 1.0),
            ))
        elif "format" in criterion_desc or "json" in criterion_desc:
            hooks.append(ScoringHook(
                id=f"hook_{i+1}",
                name=f"Format check: {criterion.get('id', f'criterion_{i+1}')}",
                hook_type="json_path",
                weight=criterion.get("weight", 1.0),
            ))
        else:
            # Default to LLM judge for complex criteria
            hooks.append(ScoringHook(
                id=f"hook_{i+1}",
                name=f"Quality check: {criterion.get('id', f'criterion_{i+1}')}",
                hook_type="llm_judge",
                llm_prompt=f"Does the response satisfy: {criterion.get('description', '')}?",
                weight=criterion.get("weight", 1.0),
            ))

    return hooks


def _extract_expected_outcome(response: str, approach: dict) -> str:
    """Extract expected outcome from LLM response or approach."""
    # Look for expected outcome in response
    lines = response.split("\n")
    for line in lines:
        if "expected" in line.lower() and "outcome" in line.lower():
            return line.split(":", 1)[-1].strip() if ":" in line else line.strip()

    # Fall back to approach expected outcome
    return approach.get("expected_outcome", "Agent responds correctly to all test prompts")


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

