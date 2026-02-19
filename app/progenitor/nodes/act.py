"""
Act Node - Executes the current work item.

Handles:
- A2A calls to existing agents
- MCP tool calls
- Copilot builds for new agents/tools
- User communication tools (say_to_user, ask_user)
"""

import inspect
import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..llm.prompts import PromptTemplates
from ..llm.response_models import BuildSpec, validate_build_spec
from ..runtime.copilot_acp import CopilotACPManager
from ..runtime.time_utils import utcnow_isoformat
from ..state.helpers import normalize_work_item
from ..state.models import ProgenitorState, WorkItem, WorkItemType
from ..tools.copilot import CopilotCLI
from ..tools.mcp import A2AClient, MCPClient
from ..tools.user_communication import UserCommunicationTools

_prompts = PromptTemplates()

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


# Type alias for event pusher callback
EventPusher = Callable[[str, str, dict[str, Any] | None], Coroutine[Any, Any, None]]


async def _request_build_spec(
    llm_client,
    item_type: str,
    description: str,
    capabilities: list[str],
) -> dict[str, Any]:
    """Request build specification from LLM using structured output."""
    try:
        inspect.getattr_static(llm_client, "chat_structured")
        has_real_chat_structured = True
    except AttributeError:
        has_real_chat_structured = False

    fallback_generate_build_spec = getattr(llm_client, "generate_build_spec", None)
    if not has_real_chat_structured and callable(fallback_generate_build_spec):
        fallback_result = fallback_generate_build_spec(
            item_type=item_type,
            description=description,
            capabilities=capabilities,
        )
        if inspect.isawaitable(fallback_result):
            fallback_result = await fallback_result
        if isinstance(fallback_result, dict):
            return fallback_result
        return {}

    system_prompt = llm_client.prompts.get_build_prompt(item_type)
    context = f"""
Build Type: {item_type}
Description: {description}
Required Capabilities: {', '.join(capabilities)}

Generate a detailed specification for building this component.
"""
    messages = [{"role": "user", "content": context}]

    if hasattr(llm_client, "chat_structured"):
        parsed = await llm_client.chat_structured(
            messages=messages,
            response_model=BuildSpec,
            schema_name="build_spec",
            system_prompt=system_prompt,
            temperature=0.5,
        )
        validated = validate_build_spec(parsed.model_dump())
        return validated.model_dump()

    response_format = llm_client._get_json_response_format(BuildSpec, "build_spec")
    response = await llm_client.chat(
        messages=messages,
        system_prompt=system_prompt,
        temperature=0.5,
        response_format=response_format,
    )
    raw_data = json.loads(response)
    validated = validate_build_spec(raw_data)
    return validated.model_dump()


def create_node(
    llm_client,
    tool_registry,
    config: "ProgenitorConfig",
    worktrees_dir: Path,
    state_manager=None,
    event_pusher: EventPusher | None = None,
    copilot_acp_manager: CopilotACPManager | None = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the act node function.

    Args:
        llm_client: LLM client for model calls.
        tool_registry: Registry of available tools/agents.
        config: ProgenitorConfig with copilot configuration.
        worktrees_dir: Path to worktrees directory.
        state_manager: Optional state manager for logging.
        event_pusher: Optional callback to push events to clients via IPC.
        copilot_acp_manager: CopilotACPManager for ACP communication.
    """
    if copilot_acp_manager is None:
        raise RuntimeError(
            "copilot_acp_manager is required for CopilotCLI. "
            "Ensure CopilotACPManager is initialized."
        )

    copilot_cli = CopilotCLI(
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        default_flags=config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        acp_manager=copilot_acp_manager,
        timeout=config.copilot.timeout_seconds,
        logs_dir=config.storage.log_dir,
    )
    mcp_client = MCPClient()
    a2a_client = A2AClient()

    # Initialize user communication tools if event_pusher is provided
    user_comm_tools: UserCommunicationTools | None = None
    if event_pusher:
        user_comm_tools = UserCommunicationTools(event_pusher=event_pusher)

    async def act_node(state: ProgenitorState) -> ProgenitorState:
        """Execute the current work item."""
        current_item = state.get("current_work_item")
        backlog = list(state.get("backlog", []))
        schedule_updated = None

        if not current_item:
            return {}

        # CQ-007: Use canonical normalize_work_item from helpers.py
        work_item = normalize_work_item(current_item)
        if work_item is None:
            logger.warning("Could not normalize work item: %s", current_item)
            return {}
        logger.info(f"Executing work item {work_item.id}: {work_item.item_type_value}")

        result = None
        error = None

        try:
            result, error = await _execute_work_item(
                work_item=work_item,
                tool_registry=tool_registry,
                a2a_client=a2a_client,
                mcp_client=mcp_client,
                copilot_cli=copilot_cli,
                llm_client=llm_client,
                worktrees_dir=worktrees_dir,
                user_comm_tools=user_comm_tools,
            )
        except Exception as e:
            logger.exception(f"Error executing work item {work_item.id}")
            error = str(e)

        # Update work item status
        if error:
            work_item.mark_failed(error)
            logger.warning(f"Work item {work_item.id} failed: {error}")
        else:
            work_item.mark_completed(result)
            logger.info(f"Work item {work_item.id} completed successfully")

            # Update schedule entry after execution
            if work_item.item_type == WorkItemType.SCHEDULED and state_manager:
                schedule_id = work_item.context.get("schedule_id")
                repeat_minutes = work_item.context.get("repeat_minutes")
                if schedule_id:
                    try:
                        from ..runtime.time_utils import utcnow
                        now = utcnow()
                        updates = {"last_run": utcnow_isoformat()}

                        # For recurring tasks, advance run_at
                        if repeat_minutes:
                            from datetime import timedelta
                            next_run = now + timedelta(minutes=repeat_minutes)
                            updates["run_at"] = next_run.isoformat()
                            logger.info(f"Advancing recurring schedule {schedule_id} to {next_run}")

                        await state_manager.update_schedule_entry(schedule_id, updates)
                        logger.info(f"Updated schedule entry {schedule_id}")

                        # Update in-memory schedule to prevent immediate re-trigger
                        schedule = list(state.get("schedule", []))
                        for i, entry in enumerate(schedule):
                            if hasattr(entry, "model_dump"):
                                entry_dict = entry.model_dump()
                            elif isinstance(entry, dict):
                                entry_dict = dict(entry)
                            else:
                                continue

                            if entry_dict.get("id") != schedule_id:
                                continue

                            entry_dict.update(updates)
                            from ..state.models import ScheduleEntry

                            schedule[i] = ScheduleEntry(**entry_dict)
                            schedule_updated = schedule
                            break
                    except Exception as e:
                        logger.warning(f"Failed to update schedule entry: {e}")

        # Update in backlog
        for i, item in enumerate(backlog):
            item_id = item.id if isinstance(item, WorkItem) else item.get("id") if isinstance(item, dict) else None
            if item_id == work_item.id:
                backlog[i] = work_item
                break

        # Capture output for VERIFY phase in inner loop
        output_for_verify = {
            "work_item": work_item.model_dump() if hasattr(work_item, "model_dump") else work_item,
            "result": result,
            "status": work_item.status_value if hasattr(work_item, "status_value") else work_item.status,
            "completed": error is None,
        }

        return cast(ProgenitorState, {
            "current_work_item": work_item,
            "backlog": backlog,
            "actions_this_cycle": state.get("actions_this_cycle", 0) + 1,
            "execution_result": output_for_verify,  # BL-006: ExecutionState field
            "work_item_completed": error is None,
            **({"schedule": schedule_updated} if schedule_updated is not None else {}),
        })

    return act_node


# CQ-007: Removed duplicate _normalize_work_item - use normalize_work_item from state.helpers


async def _execute_work_item(
    work_item: WorkItem,
    tool_registry,
    a2a_client: A2AClient,
    mcp_client: MCPClient,
    copilot_cli: CopilotCLI,
    llm_client,
    worktrees_dir: Path,
    user_comm_tools: UserCommunicationTools | None = None,
) -> tuple[dict | None, str | None]:
    """Execute a work item and return (result, error)."""
    item_type = work_item.item_type

    if item_type == WorkItemType.A2A_CALL:
        return await _execute_a2a_call(work_item, tool_registry, a2a_client)

    elif item_type == WorkItemType.MCP_CALL:
        return await _execute_mcp_call(work_item, tool_registry, mcp_client)

    elif item_type in (WorkItemType.BUILD_AGENT, WorkItemType.BUILD_TOOL):
        return await _execute_build(work_item, llm_client, copilot_cli, worktrees_dir)

    elif item_type == WorkItemType.UPGRADE:
        return await _execute_upgrade(work_item, tool_registry, copilot_cli, worktrees_dir)

    elif item_type == WorkItemType.SCHEDULED:
        return await _execute_scheduled(work_item, tool_registry, a2a_client, mcp_client, llm_client, user_comm_tools)

    elif item_type == WorkItemType.DIRECT_ANSWER:
        return {"message": "Direct answer provided"}, None

    elif item_type == WorkItemType.SYSTEM_TOOL:
        return await _execute_system_tool(work_item, tool_registry, llm_client, user_comm_tools)

    return None, f"Unknown item type: {item_type}"


async def _execute_a2a_call(
    work_item: WorkItem,
    tool_registry,
    a2a_client: A2AClient,
) -> tuple[dict | None, str | None]:
    """Execute an A2A agent call."""
    agent_config = tool_registry.find_by_name(work_item.target_agent)
    if not agent_config:
        return None, f"Agent not found: {work_item.target_agent}"

    result = await a2a_client.call_agent(
        agent_config,
        request=work_item.description,
        context=work_item.context,
    )
    return result, None


async def _execute_mcp_call(
    work_item: WorkItem,
    tool_registry,
    mcp_client: MCPClient,
) -> tuple[dict | None, str | None]:
    """Execute an MCP tool call."""
    tool_config = tool_registry.find_by_name(work_item.target_tool)
    if not tool_config:
        return None, f"Tool not found: {work_item.target_tool}"

    method = work_item.context.get("method", "execute")
    params = work_item.context.get("params", {})
    result = await mcp_client.call_tool(tool_config, method, params)
    return result, None


async def _execute_build(
    work_item: WorkItem,
    llm_client,
    copilot_cli: CopilotCLI,
    worktrees_dir: Path,
) -> tuple[dict | None, str | None]:
    """Execute a build operation via Copilot CLI.

    Consumes spec_doc.content from work_item.context (set by PLAN phase)
    to provide structured guidance to Copilot.
    """
    logger.info(f"BUILD: Starting build for work item {work_item.id}")
    capabilities = work_item.context.get("capabilities", [])

    # Get item_type value safely (might be enum or string)
    item_type_value = work_item.item_type.value if hasattr(work_item.item_type, 'value') else str(work_item.item_type)
    logger.info(f"BUILD: item_type={item_type_value}, capabilities={capabilities}")

    # =========================================================================
    # NEW APPROACH: Use copilot_prompt from PLAN (references spec file)
    # =========================================================================
    # The copilot_prompt is generated by PLAN and instructs Copilot to:
    # 1. Read the spec file from build/<...>/spec.md
    # 2. Build according to the spec
    # The spec file itself has already been dropped into build/ by PLAN

    copilot_prompt = work_item.context.get("copilot_prompt")
    spec_file_path = work_item.context.get("spec_file_path")
    spec: dict[str, Any] | None = None  # Only set in fallback path

    if copilot_prompt:
        logger.info(f"BUILD: Using copilot_prompt from PLAN ({len(copilot_prompt)} chars)")
        logger.info(f"BUILD: Spec file at {spec_file_path}")
        prompt = copilot_prompt
    else:
        # Fallback: Generate build spec the old way if no copilot_prompt
        logger.info("BUILD: No copilot_prompt from PLAN, falling back to spec generation...")
        spec = await _request_build_spec(
            llm_client=llm_client,
            item_type=item_type_value,
            description=work_item.description,
            capabilities=capabilities,
        )
        logger.info(f"BUILD: spec generated, length={len(str(spec))}")
        if spec:  # Type guard for Pylance
            logger.info(f"BUILD: spec name={spec.get('name')}, kind={spec.get('kind')}")

            # Read spec_doc content from file if spec_file_path is available
            spec_file_path = work_item.context.get("spec_file_path")
            if spec_file_path:
                try:
                    from pathlib import Path as PathLib
                    spec_path = PathLib(spec_file_path)
                    if spec_path.exists():
                        spec_doc_content = spec_path.read_text(encoding="utf-8")
                        spec["spec_doc_content"] = spec_doc_content
                        logger.info(f"BUILD: Read spec content from {spec_file_path}")
                except Exception as e:
                    logger.warning(f"BUILD: Could not read spec file: {e}")

            prompt = llm_client.prompts.get_copilot_task_prompt(spec)
            logger.info(f"BUILD: Generated fallback copilot prompt, length={len(prompt)}")
        else:
            # If spec generation failed, use a basic prompt
            prompt = f"Build an agent for: {work_item.description}"
            logger.warning("BUILD: spec generation returned empty, using basic prompt")

    logger.info(f"BUILD: Prompt preview: {prompt[:300]}...")

    # Run Copilot - now with the prompt that references the spec file
    # If worktree_path is None, use the main project directory (parent of worktrees_dir)
    working_dir = Path(work_item.worktree_path) if work_item.worktree_path else worktrees_dir.parent
    logger.info(f"BUILD: Running copilot in {working_dir}")
    allowed_dirs = None
    if isinstance(work_item.context.get("allowable_directories"), list):
        allowed_dirs = [d for d in work_item.context.get("allowable_directories", []) if isinstance(d, str)]
    if not allowed_dirs and isinstance(work_item.context.get("project_directory"), str):
        allowed_dirs = [work_item.context["project_directory"]]
    if not allowed_dirs:
        allowed_dirs = ["app"]
    build_result = await copilot_cli.build(
        work_item_id=work_item.id,
        worktree_path=working_dir,
        prompt=prompt,
        allowed_dirs=allowed_dirs,
        # Note: We no longer pass spec_file_path for stdin - the prompt references the file instead
    )
    logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

    result: dict[str, Any] = {"build_result": build_result}
    # Include spec info if we had a fallback (spec is only defined when copilot_prompt is None)
    if not copilot_prompt and spec is not None:
        result["spec"] = spec

    if not build_result.get("success"):
        return result, build_result.get("error") or build_result.get("stderr")

    return result, None


async def _execute_upgrade(
    work_item: WorkItem,
    tool_registry,
    copilot_cli: CopilotCLI,
    worktrees_dir: Path,
) -> tuple[dict | None, str | None]:
    """Execute an upgrade operation via Copilot CLI."""
    target = work_item.target_agent or work_item.target_tool
    existing = tool_registry.find_by_name(target)

    if not existing:
        return None, f"Component not found for upgrade: {target}"

    prompt = _prompts.get(
        "act.upgrade.md",
        name=existing.get('name', ''),
        capabilities=existing.get('capabilities', ''),
        description=work_item.description,
    )

    build_result = await copilot_cli.build(
        work_item_id=work_item.id,
        worktree_path=Path(work_item.worktree_path) if work_item.worktree_path else worktrees_dir,
        prompt=prompt,
        allowed_dirs=["app"],
    )

    if not build_result.get("success"):
        return {"build_result": build_result}, build_result.get("error")

    return {"build_result": build_result}, None


async def _execute_scheduled(
    work_item: WorkItem,
    tool_registry,
    a2a_client: A2AClient,
    mcp_client: MCPClient,
    llm_client,
    user_comm_tools: UserCommunicationTools | None = None,
) -> tuple[dict | None, str | None]:
    """Execute a scheduled task.

    If the schedule has an instruction, run it through the LLM as if it were
    a new user request. This allows scheduled tasks to invoke other tools,
    A2A agents, or generate responses.

    When user_comm_tools is available, the result can be sent to the user
    via IPC events.
    """
    action_type = work_item.context.get("action_type")
    action_config = work_item.context.get("action_config", {})
    instruction = work_item.context.get("instruction")

    # If we have an instruction, execute it through the LLM
    if instruction:
        logger.info(f"Executing scheduled instruction: {instruction[:100]}...")

        try:
            # Build a system prompt that includes tool instructions if available
            tool_instructions = ""
            if user_comm_tools:
                tool_instructions = _prompts.get("act.scheduled_tool_instructions.md")

            system_prompt = _prompts.get(
                "act.scheduled_system.md",
                tool_instructions=tool_instructions,
            )

            # Generate a response to the instruction as if it were a user request
            response = await llm_client.complete(
                prompt=instruction,
                system_prompt=system_prompt,
            )

            logger.info(f"Scheduled instruction completed: {response[:100] if response else 'empty'}...")

            # Process the response for user communication directives
            user_message_sent = False
            if user_comm_tools and response:
                response_lower = response.strip().lower()

                # Check for SAY directive
                if response_lower.startswith("say:"):
                    message = response[4:].strip()
                    await user_comm_tools.say_to_user(
                        message=message,
                        context=f"Scheduled task: {instruction[:50]}...",
                    )
                    user_message_sent = True
                    logger.info("Sent scheduled message to user via say_to_user")

                # Check for ASK directive
                elif response_lower.startswith("ask:"):
                    question = response[4:].strip()
                    await user_comm_tools.ask_user(
                        question=question,
                        context=f"Scheduled task: {instruction[:50]}...",
                    )
                    user_message_sent = True
                    logger.info("Sent question to user via ask_user")

                # Check for NOTIFY directive
                elif response_lower.startswith("notify:"):
                    parts = response[7:].strip().split("|", 1)
                    if len(parts) == 2:
                        level = parts[0].strip().lower()
                        body = parts[1].strip()
                        if level not in ("info", "warning", "error", "success"):
                            level = "info"
                        await user_comm_tools.notify_user(
                            title="Scheduled Task",
                            body=body,
                            level=level,
                        )
                        user_message_sent = True
                        logger.info("Sent notification to user via notify_user")

                # If no directive but response looks like a message to user, send it
                elif not user_message_sent and _looks_like_user_message(response, instruction):
                    await user_comm_tools.say_to_user(
                        message=response,
                        context=f"Scheduled task: {instruction[:50]}...",
                    )
                    user_message_sent = True
                    logger.info("Sent implied message to user via say_to_user")

            return {
                "message": "Scheduled instruction executed",
                "instruction": instruction,
                "response": response,
                "schedule_id": work_item.context.get("schedule_id"),
                "user_notified": user_message_sent,
            }, None

        except Exception as e:
            logger.error(f"Failed to execute scheduled instruction: {e}")
            return None, f"Failed to execute scheduled instruction: {e}"

    # Legacy handling for action_type based scheduling
    if action_type == "a2a_call":
        agent_config = tool_registry.find_by_name(action_config.get("agent"))
        if agent_config:
            result = await a2a_client.call_agent(
                agent_config,
                request=action_config.get("request", work_item.description),
            )
            return result, None

    elif action_type == "mcp_call":
        tool_config = tool_registry.find_by_name(action_config.get("tool"))
        if tool_config:
            result = await mcp_client.call_tool(
                tool_config,
                action_config.get("method", "execute"),
                action_config.get("params", {}),
            )
            return result, None

    return {"message": "Scheduled task executed"}, None


def _looks_like_user_message(response: str, instruction: str) -> bool:
    """Heuristic to determine if a response should be sent to the user.

    Returns True if the instruction seems to be requesting communication with the user.
    """
    instruction_lower = instruction.lower()
    user_comm_keywords = [
        "say", "tell", "reply", "respond", "message", "notify",
        "remind", "alert", "inform", "let the user know",
        "to user", "to the user", "send message",
        "hello world",  # Common test phrase
    ]
    return any(keyword in instruction_lower for keyword in user_comm_keywords)


async def _execute_system_tool(
    work_item: WorkItem,
    tool_registry,
    llm_client,
    user_comm_tools: UserCommunicationTools | None = None,
) -> tuple[dict | None, str | None]:
    """Execute an internal system tool.

    System tools are built-in operations like agent discovery, schedule management,
    and user communication tools.
    """
    tool_name = work_item.context.get("tool_name", work_item.target_tool)
    tool_params = work_item.context.get("params", {})

    # User communication tools
    if tool_name in ("say_to_user", "say", "tell", "to_user"):
        if not user_comm_tools:
            return None, "User communication tools not available"
        message = tool_params.get("message", work_item.description)
        context = tool_params.get("context")
        result = await user_comm_tools.say_to_user(message=message, context=context)
        return result, None

    elif tool_name in ("ask_user", "ask", "question"):
        if not user_comm_tools:
            return None, "User communication tools not available"
        question = tool_params.get("question", work_item.description)
        context = tool_params.get("context")
        options = tool_params.get("options")
        result = await user_comm_tools.ask_user(question=question, context=context, options=options)
        return result, None

    elif tool_name in ("notify_user", "notify", "alert"):
        if not user_comm_tools:
            return None, "User communication tools not available"
        title = tool_params.get("title", "Notification")
        body = tool_params.get("body", work_item.description)
        level = tool_params.get("level", "info")
        result = await user_comm_tools.notify_user(title=title, body=body, level=level)
        return result, None

    elif tool_name == "rediscover_agents":
        from pathlib import Path

        from ..tools.agent_discovery import discover_agents

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent
        agents_dir = project_root / "build" / "agents"

        logger.info("Executing system tool: rediscover_agents")

        result = await discover_agents(
            agents_dir=agents_dir,
            llm_client=llm_client,
            registry=tool_registry,
        )

        return result, None

    elif tool_name == "list_agents":
        # List all registered agents with their status
        agents = await tool_registry.get_running_agents()
        all_agents = []
        data = tool_registry._data

        for agent in data.get("agents", []):
            all_agents.append({
                "name": agent.get("name"),
                "description": agent.get("description"),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
            })

        return {
            "agents": all_agents,
            "running_count": len(agents),
            "total_count": len(all_agents),
        }, None

    elif tool_name == "schedule_task":
        # Add a scheduled task - returns entry data to be added to state
        from ..tools.schedule import add_scheduled_task

        tool_params = work_item.context.get("tool_params", {})

        schedule_result, entry_data = await add_scheduled_task(
            user_request=tool_params.get("user_request", ""),
            time_expression=tool_params.get("time_expression", ""),
            instruction=tool_params.get("instruction", ""),
            description=tool_params.get("description"),
        )

        if schedule_result.success and entry_data:
            # Return the entry data to be added to schedule by the caller
            return {
                "result": schedule_result.model_dump(),
                "schedule_entry": entry_data,
                "add_to_schedule": True,  # Signal to add this entry
            }, None
        else:
            return {"result": schedule_result.model_dump()}, None if schedule_result.success else schedule_result.message

    elif tool_name == "list_schedule":

        # This would need the schedule from state - return instruction to check state
        return {
            "message": "Use get_schedule from state manager to list scheduled tasks",
            "tool": "list_schedule",
        }, None

    elif tool_name == "current_time":
        return {
            "utc_time": utcnow_isoformat(),
            "timezone": "UTC",
        }, None

    elif tool_name == "list_tools":
        # Delegate to the canonical system tool implementation so this matches
        # decide-node fast/slow path output (and includes MCP discovery sources).
        from ..tools.list_tools import ListToolsTool
        from ..tools.protocol import ToolContext as SystemToolContext

        ctx = SystemToolContext(
            tool_registry=tool_registry,
            llm_client=llm_client,
            state=None,
            state_manager=None,
            user_input=work_item.description,
        )

        tool_result = await ListToolsTool().execute(ctx)
        if tool_result.success:
            return tool_result.data, None
        return None, tool_result.error or "list_tools failed"

    return None, f"Unknown system tool: {tool_name}"
