"""
Act Node - Executes the current work item.

Handles:
- A2A calls to existing agents
- MCP tool calls
- Copilot builds for new agents/tools
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Any, TYPE_CHECKING

from ..state.models import ProgenitorState, WorkItem, WorkItemStatus, WorkItemType
from ..state.helpers import normalize_work_item
from ..tools.copilot import CopilotCLI
from ..tools.mcp import MCPClient, A2AClient
from ..runtime.time_utils import utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    tool_registry,
    config: "ProgenitorConfig",
    worktrees_dir: Path,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the act node function.

    Args:
        llm_client: LLM client for model calls.
        tool_registry: Registry of available tools/agents.
        config: ProgenitorConfig with copilot configuration.
        worktrees_dir: Path to worktrees directory.
        state_manager: Optional state manager for logging.
    """
    copilot_cli = CopilotCLI(
        config_path=config.copilot.config_path,
        timeout=config.copilot.timeout_seconds,
    )
    mcp_client = MCPClient()
    a2a_client = A2AClient()

    async def act_node(state: ProgenitorState) -> ProgenitorState:
        """Execute the current work item."""
        current_item = state.get("current_work_item")
        backlog = list(state.get("backlog", []))

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

        return {
            "current_work_item": work_item,
            "backlog": backlog,
            "actions_this_cycle": state.get("actions_this_cycle", 0) + 1,
            "last_iteration_output": output_for_verify,
            "work_item_completed": error is None,
        }

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
) -> tuple[dict | None, str | None]:
    """Execute a work item and return (result, error)."""
    item_type = work_item.item_type

    if item_type == WorkItemType.A2A_CALL:
        return await _execute_a2a_call(work_item, tool_registry, a2a_client)

    elif item_type == WorkItemType.MCP_CALL:
        return await _execute_mcp_call(work_item, tool_registry, mcp_client)

    elif item_type in (WorkItemType.BUILD_AGENT, WorkItemType.BUILD_MCP):
        return await _execute_build(work_item, llm_client, copilot_cli, worktrees_dir)

    elif item_type == WorkItemType.UPGRADE:
        return await _execute_upgrade(work_item, tool_registry, copilot_cli, worktrees_dir)

    elif item_type == WorkItemType.SCHEDULED:
        return await _execute_scheduled(work_item, tool_registry, a2a_client, mcp_client, llm_client)

    elif item_type == WorkItemType.DIRECT_ANSWER:
        return {"message": "Direct answer provided"}, None

    elif item_type == WorkItemType.SYSTEM_TOOL:
        return await _execute_system_tool(work_item, tool_registry, llm_client)

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
    # 1. Read the spec file from app/agents/<agent_name>_spec.md
    # 2. Build according to the spec
    # The spec file itself has already been dropped into app/agents/ by PLAN

    copilot_prompt = work_item.context.get("copilot_prompt")
    spec_file_path = work_item.context.get("spec_file_path")

    if copilot_prompt:
        logger.info(f"BUILD: Using copilot_prompt from PLAN ({len(copilot_prompt)} chars)")
        logger.info(f"BUILD: Spec file at {spec_file_path}")
        prompt = copilot_prompt
    else:
        # Fallback: Generate build spec the old way if no copilot_prompt
        logger.info("BUILD: No copilot_prompt from PLAN, falling back to spec generation...")
        spec = await llm_client.generate_build_spec(
            item_type=item_type_value,
            description=work_item.description,
            capabilities=capabilities,
        )
        logger.info(f"BUILD: spec generated, length={len(str(spec))}")
        logger.info(f"BUILD: spec name={spec.get('name')}, kind={spec.get('kind')}")

        # Include spec_doc content in fallback
        spec_doc_content = ""
        spec_doc_data = work_item.context.get("spec_doc")
        if spec_doc_data:
            spec_doc_content = spec_doc_data.get("content", "")
            spec["spec_doc_content"] = spec_doc_content

        prompt = llm_client.prompts.get_copilot_task_prompt(spec)
        logger.info(f"BUILD: Generated fallback copilot prompt, length={len(prompt)}")

    logger.info(f"BUILD: Prompt preview: {prompt[:300]}...")

    # Run Copilot - now with the prompt that references the spec file
    # If worktree_path is None, use the main project directory (parent of worktrees_dir)
    working_dir = Path(work_item.worktree_path) if work_item.worktree_path else worktrees_dir.parent
    logger.info(f"BUILD: Running copilot in {working_dir}")
    build_result = await copilot_cli.build(
        work_item_id=work_item.id,
        worktree_path=working_dir,
        prompt=prompt,
        # Note: We no longer pass spec_file_path for stdin - the prompt references the file instead
    )
    logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

    result = {"build_result": build_result}
    # Include spec info if we had a fallback
    if not copilot_prompt:
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

    prompt = f"""Upgrade the following component:
Name: {existing.get('name')}
Current capabilities: {existing.get('capabilities')}

Requested changes: {work_item.description}

Keep changes minimal and focused.
"""

    build_result = await copilot_cli.build(
        work_item_id=work_item.id,
        worktree_path=Path(work_item.worktree_path) if work_item.worktree_path else worktrees_dir,
        prompt=prompt,
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
) -> tuple[dict | None, str | None]:
    """Execute a scheduled task.

    If the schedule has an instruction, run it through the LLM as if it were
    a new user request. This allows scheduled tasks to invoke other tools,
    A2A agents, or generate responses.
    """
    action_type = work_item.context.get("action_type")
    action_config = work_item.context.get("action_config", {})
    instruction = work_item.context.get("instruction")

    # If we have an instruction, execute it through the LLM
    if instruction:
        logger.info(f"Executing scheduled instruction: {instruction[:100]}...")

        try:
            # Generate a response to the instruction as if it were a user request
            # This can trigger further actions like A2A calls based on LLM decision
            response = await llm_client.complete(
                prompt=instruction,
                system_prompt="""You are Progenitor, an AI assistant executing a scheduled task.
The following is an instruction that was scheduled to run at this time.
Execute the instruction and provide the response.
If the instruction requires calling another agent or tool, indicate that in your response.""",
            )

            logger.info(f"Scheduled instruction completed: {response[:100] if response else 'empty'}...")

            return {
                "message": "Scheduled instruction executed",
                "instruction": instruction,
                "response": response,
                "schedule_id": work_item.context.get("schedule_id"),
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


async def _execute_system_tool(
    work_item: WorkItem,
    tool_registry,
    llm_client,
) -> tuple[dict | None, str | None]:
    """Execute an internal system tool.

    System tools are built-in operations like agent discovery and schedule management.
    """
    tool_name = work_item.context.get("tool_name", work_item.target_tool)

    if tool_name == "rediscover_agents":
        from ..tools.agent_discovery import discover_agents
        from pathlib import Path

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent
        agents_dir = project_root / "app" / "agents"

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

        result, entry_data = await add_scheduled_task(
            user_request=tool_params.get("user_request", ""),
            time_expression=tool_params.get("time_expression", ""),
            instruction=tool_params.get("instruction", ""),
            description=tool_params.get("description"),
            recurring=tool_params.get("recurring", False),
        )

        if result.success and entry_data:
            # Return the entry data to be added to schedule by the caller
            return {
                "result": result.model_dump(),
                "schedule_entry": entry_data,
                "add_to_schedule": True,  # Signal to add this entry
            }, None
        else:
            return {"result": result.model_dump()}, None if result.success else result.message

    elif tool_name == "list_schedule":
        from ..tools.schedule import list_scheduled_tasks

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
        # List all available tools including system tools, agents, and MCP tools
        system_tools = [
            {
                "name": "list_tools",
                "description": "List all available tools and their capabilities",
                "type": "system_tool",
                "capabilities": ["tool_discovery", "list_tools"],
            },
            {
                "name": "rediscover_agents",
                "description": "Scan the agents folder, analyze with LLM, and update registry.json",
                "type": "system_tool",
                "capabilities": ["agent_discovery", "registry_refresh"],
            },
            {
                "name": "list_agents",
                "description": "List all registered agents with their status",
                "type": "system_tool",
                "capabilities": ["agent_listing"],
            },
            {
                "name": "schedule_task",
                "description": "Schedule a task to run at a specific time",
                "type": "system_tool",
                "capabilities": ["scheduling", "reminders"],
            },
            {
                "name": "list_schedule",
                "description": "List all scheduled tasks",
                "type": "system_tool",
                "capabilities": ["scheduling"],
            },
            {
                "name": "current_time",
                "description": "Return the current UTC time",
                "type": "system_tool",
                "capabilities": ["time", "clock"],
            },
        ]

        # Get registered agents from registry
        agents = tool_registry.get_agents()
        agent_tools = [
            {
                "name": agent.get("name"),
                "description": agent.get("description", ""),
                "type": "a2a_agent",
                "capabilities": agent.get("capabilities", []),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
        ]

        # Get registered MCP tools from registry
        mcp_tools = tool_registry.get_tools()
        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
                "capabilities": tool.get("capabilities", []),
            }
            for tool in mcp_tools
        ]

        return {
            "system_tools": system_tools,
            "agents": agent_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(agent_tools) + len(mcp_tool_entries),
            "summary": {
                "system_tools": len(system_tools),
                "agents": len(agent_tools),
                "mcp_tools": len(mcp_tool_entries),
            },
        }, None

    return None, f"Unknown system tool: {tool_name}"
