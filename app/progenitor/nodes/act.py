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
        return await _execute_scheduled(work_item, tool_registry, a2a_client, mcp_client)

    elif item_type == WorkItemType.DIRECT_ANSWER:
        return {"message": "Direct answer provided"}, None

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
    """Execute a build operation via Copilot CLI."""
    logger.info(f"BUILD: Starting build for work item {work_item.id}")
    capabilities = work_item.context.get("capabilities", [])

    # Get item_type value safely (might be enum or string)
    item_type_value = work_item.item_type.value if hasattr(work_item.item_type, 'value') else str(work_item.item_type)
    logger.info(f"BUILD: item_type={item_type_value}, capabilities={capabilities}")

    # Generate build spec
    logger.info("BUILD: Generating build spec...")
    spec = await llm_client.generate_build_spec(
        item_type=item_type_value,
        description=work_item.description,
        capabilities=capabilities,
    )
    logger.info(f"BUILD: spec generated, length={len(str(spec))}")
    logger.info(f"BUILD: spec name={spec.get('name')}, kind={spec.get('kind')}")

    # Generate prompt for Copilot
    prompt = llm_client.prompts.get_copilot_task_prompt(spec)
    logger.info(f"BUILD: Generated copilot prompt, length={len(prompt)}")
    logger.info(f"BUILD: Prompt preview: {prompt[:300]}...")

    # Run Copilot
    # If worktree_path is None, use the main project directory (parent of worktrees_dir)
    working_dir = Path(work_item.worktree_path) if work_item.worktree_path else worktrees_dir.parent
    logger.info(f"BUILD: Running copilot in {working_dir}")
    build_result = await copilot_cli.build(
        work_item_id=work_item.id,
        worktree_path=working_dir,
        prompt=prompt,
    )
    logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

    result = {"build_result": build_result, "spec": spec}

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
) -> tuple[dict | None, str | None]:
    """Execute a scheduled task."""
    action_type = work_item.context.get("action_type")
    action_config = work_item.context.get("action_config", {})

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
