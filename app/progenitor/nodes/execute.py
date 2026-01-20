"""
EXECUTE Node - Execute tools if needed (loop.spec.md).

Separate from BUILD phase per spec:
- BUILD produces candidate output
- EXECUTE runs tools if needed

This separation allows verification of BUILD output before tool execution,
and clean separation of concerns.
"""

import logging
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItem, WorkItemStatus
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


def create_node(
    tool_registry,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the execute node function.

    Args:
        tool_registry: Registry of available tools for execution.
        state_manager: Optional state manager for logging.
    """

    async def execute_node(state: ProgenitorState) -> ProgenitorState:
        """
        EXECUTE phase: Run tools if needed.

        Per loop.spec.md:
        - BUILD produces candidate output
        - EXECUTE runs tools if needed

        This node handles the actual tool execution after BUILD
        has prepared the work.
        """
        current_item = state.get("current_work_item")
        build_output = state.get("last_iteration_output")

        if not current_item:
            logger.debug("No work item to execute")
            return {}

        # Check if execution is needed (some work items are already complete after BUILD)
        work_completed = state.get("work_item_completed", False)
        if work_completed:
            logger.info("Work item already completed in BUILD phase")
            return {
                "execution_result": build_output,
                "execution_completed": True,
            }

        iteration = state.get("current_iteration", 0)

        # Get pending tools from BUILD phase
        pending_tools = state.get("pending_tool_calls", [])

        if not pending_tools:
            # No tools to execute - pass through build output
            logger.debug("No pending tools to execute")
            return {
                "execution_result": build_output,
                "execution_completed": True,
            }

        logger.info(f"Executing {len(pending_tools)} pending tool(s)")

        tool_results = []
        errors = []

        for tool_call in pending_tools:
            try:
                tool_name = tool_call.get("tool")
                tool_args = tool_call.get("arguments", {})

                # Look up tool in registry
                tool_info = tool_registry.get_tool(tool_name)

                if tool_info:
                    # Execute the tool
                    result = await _execute_tool(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_registry=tool_registry,
                    )
                    tool_results.append({
                        "tool": tool_name,
                        "result": result,
                        "success": True,
                    })
                else:
                    errors.append(f"Tool not found: {tool_name}")
                    tool_results.append({
                        "tool": tool_name,
                        "result": None,
                        "success": False,
                        "error": f"Tool not found: {tool_name}",
                    })

            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                errors.append(str(e))
                tool_results.append({
                    "tool": tool_call.get("tool", "unknown"),
                    "result": None,
                    "success": False,
                    "error": str(e),
                })

        # Log execution
        if state_manager:
            await state_manager.log_decision({
                "event": "execute_phase",
                "iteration": iteration,
                "tools_executed": len(pending_tools),
                "tools_succeeded": sum(1 for r in tool_results if r.get("success")),
                "errors": errors[:3] if errors else None,
                "timestamp": utcnow_isoformat(),
            })

        # Combine build output with execution results
        execution_output = {
            "build_output": build_output,
            "tool_results": tool_results,
            "all_succeeded": len(errors) == 0,
            "errors": errors,
        }

        logger.info(f"Execution complete: {len(tool_results)} tools, {len(errors)} errors")

        return {
            "execution_result": execution_output,
            "last_iteration_output": execution_output,  # Update for VERIFY phase
            "execution_completed": True,
            "pending_tool_calls": [],  # Clear pending tools
            "actions_this_cycle": state.get("actions_this_cycle", 0) + len(pending_tools),
        }

    return execute_node


async def _execute_tool(tool_name: str, tool_args: dict, tool_registry) -> Any:
    """Execute a single tool and return result."""
    # Get tool from registry
    tool = tool_registry.get_tool(tool_name)

    if not tool:
        raise ValueError(f"Tool {tool_name} not found in registry")

    # Tools can be MCP servers, A2A agents, or local functions
    tool_type = tool.get("type", "function")

    if tool_type == "mcp":
        # Execute via MCP
        from ..tools.mcp import MCPClient
        client = MCPClient()
        # MCP call_tool expects (tool_config, method, params)
        tool_config = {
            "command": tool.get("command"),
            "transport": tool.get("transport", "stdio"),
        }
        method = tool_args.get("method", "execute")
        params = tool_args.get("params", {})
        return await client.call_tool(tool_config, method, params)

    elif tool_type == "a2a":
        # Execute via A2A
        from ..tools.mcp import A2AClient
        client = A2AClient()
        endpoint = tool.get("endpoint", "")
        request = tool_args.get("request", "")
        return await client.call_agent(endpoint, str(request))

    elif tool_type == "function":
        # Execute local function
        func = tool.get("function")
        if func and callable(func):
            if hasattr(func, "__call__"):
                # Check if async
                import asyncio
                if asyncio.iscoroutinefunction(func):
                    return await func(**tool_args)
                else:
                    return func(**tool_args)
        raise ValueError(f"Tool {tool_name} has no callable function")

    else:
        raise ValueError(f"Unknown tool type: {tool_type}")
