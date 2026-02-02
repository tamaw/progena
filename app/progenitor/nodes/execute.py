"""
EXECUTE Node - Execute tools if needed (loop.spec.md).

BL-004: Implements "agent exercise harness" - starts the built agent
(HTTP preferred, stdio fallback), runs the prompt suite, captures
transcripts + timing + errors into ExecutionResult.

At least one "smoke" prompt always runs; multiple prompts run when
provided by experiment_plan.

Separate from BUILD phase per spec:
- BUILD produces candidate output
- EXECUTE runs tools if needed

This separation allows verification of BUILD output before tool execution,
and clean separation of concerns.
"""

import logging
import time
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItem, WorkItemStatus
from ..state.execution_schemas import (
    ExecutionResult,
    ExecutionRun,
    ExecutionStatus,
    ExperimentPlan,
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


def create_node(
    tool_registry,
    state_manager=None,
    project_root=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the execute node function.

    Args:
        tool_registry: Registry of available tools for execution.
        state_manager: Optional state manager for logging.
        project_root: Project root path for agent harness.
    """
    from pathlib import Path
    from ..runtime.exercise_harness import AgentExerciseHarness, create_execution_result_from_build

    # Initialize exercise harness for agent testing
    if project_root:
        harness = AgentExerciseHarness(
            project_root=Path(project_root),
            state_manager=state_manager,
        )
    else:
        harness = None

    async def execute_node(state: ProgenitorState) -> ProgenitorState:
        """
        EXECUTE phase: Run tools if needed, exercise built agents.

        BL-004: Implements agent exercise harness:
        - Starts the built agent (HTTP preferred, stdio fallback)
        - Runs the prompt suite from experiment_plan
        - Captures transcripts + timing + errors into ExecutionResult
        - At least one "smoke" prompt always runs

        Per loop.spec.md:
        - BUILD produces candidate output
        - EXECUTE runs tools/exercises agents

        This node handles the actual tool execution after BUILD
        has prepared the work.
        """
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        current_item = state.get("current_work_item")
        build_output = state.get("last_iteration_output")
        iteration = state.get("current_iteration", 0)
        experiment_plan_data = state.get("experiment_plan")  # BL-002: From PLAN phase

        if tracer:
            span = tracer.start_span(
                "node_execute",
                attributes={
                    "iteration": iteration,
                    "has_work_item": current_item is not None,
                    "has_build_output": build_output is not None,
                    "has_experiment_plan": experiment_plan_data is not None,
                },
            )

        if not current_item:
            logger.debug("No work item to execute")
            if span:
                span.set_attribute("skipped", True)
                span.end()
            # BL-001: Still produce ExecutionResult even when skipped
            exec_result = ExecutionResult(
                iteration=iteration,
                status=ExecutionStatus.SKIPPED,
            ).finalize()
            return {"execution_result": exec_result.model_dump()}

        # Check if execution is needed (some work items are already complete after BUILD)
        work_completed = state.get("work_item_completed", False)
        if work_completed:
            logger.info("Work item already completed in BUILD phase")
            if span:
                span.set_attribute("already_completed", True)
                span.end()
            # BL-001: Wrap in ExecutionResult
            exec_result = await create_execution_result_from_build(
                build_output or {},
                iteration,
                current_item,
            )
            return {
                "execution_result": exec_result.model_dump(),
                "execution_completed": True,
            }

        # Check if this is an agent-type work item that needs exercising
        item_type = _get_item_type(current_item)
        is_agent_item = item_type in ("build_agent", "a2a_call")

        # BL-004: If we have an experiment plan and harness, exercise the agent
        if experiment_plan_data and harness and is_agent_item:
            logger.info("Executing agent exercise harness with experiment plan")
            exec_result = await _exercise_agent(
                harness=harness,
                experiment_plan_data=experiment_plan_data,
                current_item=current_item,
                build_output=build_output,
                iteration=iteration,
                state=state,
            )

            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("node_duration_seconds", duration, {"node": "execute"})
                metrics.increment("node_invocations_total", {"node": "execute", "success": str(exec_result.smoke_test_passed).lower()})

            if span:
                span.set_attribute("exercise_harness", True)
                span.set_attribute("prompts_total", exec_result.runs[0].prompts_total if exec_result.runs else 0)
                span.set_attribute("smoke_test_passed", exec_result.smoke_test_passed)
                span.end()

            return {
                "execution_result": exec_result.model_dump(),
                "last_iteration_output": exec_result.model_dump(),
                "execution_completed": True,
                "build_phase": "execute",
            }

        # Get pending tools from BUILD phase
        pending_tools = state.get("pending_tool_calls", [])

        if not pending_tools:
            # No tools to execute - create ExecutionResult from build output
            logger.debug("No pending tools to execute")
            if metrics:
                metrics.increment("node_invocations_total", {"node": "execute", "success": "true"})
            if span:
                span.set_attribute("no_tools", True)
                span.end()

            # BL-001: Always produce ExecutionResult
            exec_result = await create_execution_result_from_build(
                build_output or {},
                iteration,
                current_item,
            )
            return {
                "execution_result": exec_result.model_dump(),
                "last_iteration_output": exec_result.model_dump(),
                "execution_completed": True,
            }

        logger.info(f"Executing {len(pending_tools)} pending tool(s)")

        if span:
            span.set_attribute("pending_tools_count", len(pending_tools))
            span.add_event("execution_start", {"tool_count": len(pending_tools)})

        tool_results = []
        errors = []

        for tool_call in pending_tools:
            tool_start = time.perf_counter()
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

                    # Track tool call in current_state for session history
                    if state_manager:
                        await state_manager.add_tool_call({
                            "tool": tool_name,
                            "arguments": tool_args,
                            "result": str(result)[:500] if result else None,  # Truncate large results
                            "success": True,
                            "timestamp": utcnow_isoformat(),
                            "iteration": iteration,
                        })

                    if metrics:
                        tool_duration = time.perf_counter() - tool_start
                        metrics.record_histogram("tool_execution_duration_seconds", tool_duration, {"tool": tool_name})
                        metrics.increment("tool_executions_total", {"tool": tool_name, "success": "true"})
                else:
                    errors.append(f"Tool not found: {tool_name}")
                    tool_results.append({
                        "tool": tool_name,
                        "result": None,
                        "success": False,
                        "error": f"Tool not found: {tool_name}",
                    })
                    if metrics:
                        metrics.increment("tool_executions_total", {"tool": tool_name, "success": "false"})

            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                errors.append(str(e))
                tool_results.append({
                    "tool": tool_call.get("tool", "unknown"),
                    "result": None,
                    "success": False,
                    "error": str(e),
                })
                if metrics:
                    metrics.increment("tool_executions_total", {"tool": tool_call.get("tool", "unknown"), "success": "false"})

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

        # Record overall metrics
        duration = time.perf_counter() - start_time
        if metrics:
            metrics.record_histogram("node_duration_seconds", duration, {"node": "execute"})
            metrics.increment("node_invocations_total", {"node": "execute", "success": str(len(errors) == 0).lower()})

        if span:
            span.set_attribute("tools_succeeded", sum(1 for r in tool_results if r.get("success")))
            span.set_attribute("tools_failed", len(errors))
            span.set_attribute("duration_seconds", duration)
            span.add_event("execution_complete", {"errors": len(errors)})
            span.end()

        # BL-001: Wrap tool execution results in ExecutionResult
        exec_result = ExecutionResult(
            iteration=iteration,
            build_output=execution_output,
            status=ExecutionStatus.SUCCESS if len(errors) == 0 else ExecutionStatus.PARTIAL,
            all_errors=errors,
        ).finalize()

        return {
            "execution_result": exec_result.model_dump(),
            "last_iteration_output": exec_result.model_dump(),  # Update for VERIFY phase
            "execution_completed": True,
            "pending_tool_calls": [],  # Clear pending tools
            "actions_this_cycle": state.get("actions_this_cycle", 0) + len(pending_tools),
            "build_phase": "execute",
        }

    return execute_node


def _get_item_type(work_item: Any) -> str:
    """Get the item type as a string from a work item."""
    if hasattr(work_item, "item_type"):
        item_type = work_item.item_type
        return item_type.value if hasattr(item_type, "value") else str(item_type)
    elif isinstance(work_item, dict):
        item_type = work_item.get("item_type", "direct_answer")
        return item_type.value if hasattr(item_type, "value") else str(item_type)
    return "direct_answer"


async def _exercise_agent(
    harness,
    experiment_plan_data: dict,
    current_item: Any,
    build_output: Any,
    iteration: int,
    state: ProgenitorState,
) -> ExecutionResult:
    """
    Exercise an agent using the experiment harness.

    BL-004: Runs the prompt suite from experiment_plan.
    """
    # Parse experiment plan
    experiment_plan = ExperimentPlan(**experiment_plan_data)

    # Get agent endpoint/entrypoint from context or registry
    agent_endpoint = None
    agent_entrypoint = None

    # Check build output for agent info
    if isinstance(build_output, dict):
        agent_endpoint = build_output.get("agent_endpoint")
        agent_entrypoint = build_output.get("agent_entrypoint")

    # Check work item context
    if hasattr(current_item, "context"):
        context = current_item.context
    elif isinstance(current_item, dict):
        context = current_item.get("context", {})
    else:
        context = {}

    if not agent_endpoint:
        agent_endpoint = context.get("agent_endpoint")
    if not agent_entrypoint:
        agent_entrypoint = context.get("agent_entrypoint")

    # Check registry for agent info
    agents = state.get("agents", [])
    for agent in agents:
        if hasattr(agent, "name") and agent.name == experiment_plan.agent_name:
            agent_endpoint = agent_endpoint or getattr(agent, "endpoint", None)
        elif isinstance(agent, dict) and agent.get("name") == experiment_plan.agent_name:
            agent_endpoint = agent_endpoint or agent.get("endpoint")

    # Run the experiment
    result = await harness.execute_experiment(
        experiment_plan=experiment_plan,
        agent_endpoint=agent_endpoint,
        agent_entrypoint=agent_entrypoint,
        iteration=iteration,
        task_id=state.get("task_id"),
    )

    return result


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
