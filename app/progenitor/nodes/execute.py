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
from typing import Callable, Coroutine, Any

from ..state.models import ProgenitorState, WorkItem, WorkItemStatus
from ..state.helpers import mark_work_item_completed, find_work_item_by_id
from ..state.execution_schemas import (
    ExecutionResult,
    ExecutionRun,
    ExecutionStatus,
    ExperimentPlan,
)
from ..runtime.time_utils import utcnow_isoformat, utcnow
from ..runtime.port_utils import find_available_port_async, TEST_HARNESS_PORT_START, TEST_HARNESS_PORT_END

logger = logging.getLogger(__name__)


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
        logger.info("EXECUTE: Starting execute phase")

        current_item = state.get("current_work_item")
        phases = state.get("phases", {})
        build_output = phases.get("execute", {}).get("result")
        iteration = state.get("iteration", {}).get("current", 0)
        experiment_plan_data = phases.get("plan", {}).get("experiment_plan")

        if not current_item:
            # Check if this is an agent build that needs exercising even without work item
            build_settings = state.get("build_settings")
            is_agent_build = False
            if build_settings:
                build_type = build_settings.get("build_type") if isinstance(build_settings, dict) else getattr(build_settings, "build_type", None)
                if build_type == "agent":
                    is_agent_build = True
                    logger.info("Detected agent build from build_settings, proceeding with exercise")

            # Also check experiment_plan
            if experiment_plan_data and not is_agent_build:
                exp_agent_name = experiment_plan_data.get("agent_name")
                if exp_agent_name and exp_agent_name != "unknown_agent":
                    is_agent_build = True
                    logger.info("Detected agent build from experiment_plan.agent_name, proceeding with exercise")

            if is_agent_build and experiment_plan_data and harness:
                logger.info("Executing agent exercise harness for build without work item")
                exec_result = await _exercise_agent(
                    harness=harness,
                    experiment_plan_data=experiment_plan_data,
                    current_item=None,  # No work item for builds
                    build_output=build_output,
                    iteration=iteration,
                    state=state,
                )

                return {
                    "phases": {
                        "execute": {
                            "completed": True,
                            "result": exec_result.model_dump(),
                        },
                    },
                    "build_phase": "execute",
                }

            # No work item and not an agent build
            logger.debug("No work item to execute and not an agent build")
            # BL-001: Still produce ExecutionResult even when skipped
            exec_result = ExecutionResult(
                iteration=iteration,
                status=ExecutionStatus.SKIPPED,
            ).finalize()
            return {
                "phases": {
                    "execute": {
                        "completed": False,
                        "result": exec_result.model_dump(),
                    },
                },
            }

        # Check if execution is needed (some work items are already complete after BUILD)
        work_completed = state.get("work_item_completed", False)
        if work_completed:
            logger.info("Work item already completed in BUILD phase")
            # BL-001: Wrap in ExecutionResult
            exec_result = await create_execution_result_from_build(
                build_output or {},
                iteration,
                current_item,
            )
            return {
                "phases": {
                    "execute": {
                        "completed": True,
                        "result": exec_result.model_dump(),
                    },
                },
            }

        # Check if this is an agent-type work item that needs exercising
        item_type = _get_item_type(current_item)
        is_agent_item = item_type in ("build_agent", "a2a_call")

        # Also check if we have build_settings indicating an agent build
        # (fixes issue where item_type defaults to direct_answer)
        build_settings = state.get("build_settings")
        if build_settings and not is_agent_item:
            build_type = build_settings.get("build_type") if isinstance(build_settings, dict) else getattr(build_settings, "build_type", None)
            if build_type == "agent":
                is_agent_item = True
                logger.info("Detected agent build from build_settings despite item_type=%s", item_type)

        # Also check if experiment_plan has an agent_name (indicates agent build)
        if experiment_plan_data and not is_agent_item:
            exp_agent_name = experiment_plan_data.get("agent_name")
            if exp_agent_name and exp_agent_name != "unknown_agent":
                is_agent_item = True
                logger.info("Detected agent build from experiment_plan.agent_name=%s", exp_agent_name)

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

            # Mark current work item as completed (run finished, regardless of test outcome)
            # Note: BUILD already marks items completed, but this is a safety net
            backlog = list(state.get("backlog", []))
            if current_item:
                item_id = current_item.id if hasattr(current_item, "id") else current_item.get("id") if isinstance(current_item, dict) else None
                if item_id:
                    found_item = find_work_item_by_id(backlog, item_id)
                    if found_item:
                        mark_work_item_completed(found_item, exec_result.model_dump())
                        logger.info(f"EXECUTE: Marked work item {item_id} as completed")

            # Build compact execution result for nested structure
            exec_result_dict = exec_result.model_dump()
            compact_result = {
                "task_id": exec_result_dict.get("task_id"),
                "status": exec_result_dict.get("status"),
                "tests_passed": exec_result_dict.get("build_output", {}).get("tests_passed", 0) if exec_result_dict.get("build_output") else 0,
                "tests_total": exec_result_dict.get("build_output", {}).get("tests_total", 0) if exec_result_dict.get("build_output") else 0,
                "duration_ms": exec_result_dict.get("total_duration_ms"),
                "smoke_passed": exec_result_dict.get("smoke_test_passed"),
                "errors": exec_result_dict.get("all_errors", [])[:3],  # Keep only first 3 errors
            }

            return {
                "phases": {
                    "execute": {
                        "completed": True,
                        "result": compact_result,
                    },
                },
                "build_phase": "execute",
                "backlog": backlog,
                "current_work_item": None,
                # BL-006: Set execution_result at top level for verify node to consume
                "execution_result": exec_result_dict,
            }

        # Get pending tools from BUILD phase
        pending_tools = state.get("pending_tool_calls", [])

        if not pending_tools:
            # No tools to execute - create ExecutionResult from build output
            logger.debug("No pending tools to execute")

            # BL-001: Always produce ExecutionResult
            exec_result = await create_execution_result_from_build(
                build_output or {},
                iteration,
                current_item,
            )
            return {
                "phases": {
                    "execute": {
                        "completed": True,
                        "result": exec_result.model_dump(),
                    },
                },
            }

        logger.info(f"Executing {len(pending_tools)} pending tool(s)")

        tool_results = []
        errors = []

        for tool_call in pending_tools:
            try:
                tool_name: str = tool_call.get("tool", "unknown")
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
                failed_tool_name = str(tool_call.get("tool", "unknown"))
                tool_results.append({
                    "tool": failed_tool_name,
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

        # BL-001: Wrap tool execution results in ExecutionResult
        exec_result = ExecutionResult(
            iteration=iteration,
            build_output=execution_output,
            status=ExecutionStatus.SUCCESS if len(errors) == 0 else ExecutionStatus.PARTIAL,
            all_errors=errors,
        ).finalize()

        return {
            "phases": {
                "execute": {
                    "completed": True,
                    "result": exec_result.model_dump(),
                },
            },
            "pending_tool_calls": [],
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

    Enhanced flow:
    1. If agent_endpoint is available -> use traditional HTTP exercise
    2. If agent_entrypoint is available -> spawn agent, run test script
    3. Otherwise -> try to discover entrypoint from build output

    Uses TEST_HARNESS_PORT_START (9500+) range to avoid conflicts with:
    - Backend API server (8000)
    - Production A2A agents (8080-8199)
    """
    # Parse experiment plan
    experiment_plan = ExperimentPlan(**experiment_plan_data)

    # Get agent endpoint/entrypoint from context or registry
    agent_endpoint = None
    agent_entrypoint = None
    agent_port = TEST_HARNESS_PORT_START  # Use test harness port range

    # Get project root from harness for path resolution
    project_root = harness.project_root if harness else None

    # Check build output for agent info
    if isinstance(build_output, dict):
        agent_endpoint = build_output.get("agent_endpoint")
        agent_entrypoint = build_output.get("agent_entrypoint")
        agent_port = build_output.get("agent_port", TEST_HARNESS_PORT_START)

    # Try to discover entrypoint from build settings or experiment plan
    if not agent_entrypoint:
        # Look for common agent entrypoint patterns
        agent_name = experiment_plan.agent_name
        if not agent_name and isinstance(build_output, dict):
            agent_name = build_output.get("agent_name")

        # Also check build_settings for agent name
        build_settings = state.get("build_settings")
        if not agent_name and build_settings:
            agent_name = build_settings.get("project_name") if isinstance(build_settings, dict) else getattr(build_settings, "project_name", None)

        if agent_name and project_root:
            from pathlib import Path
            # Check for standard agent locations relative to project root
            potential_paths = [
                project_root / f"app/agents/{agent_name}/agent.py",
                project_root / f"app/agents/{agent_name}/main.py",
                project_root / f"app/agents/{agent_name}/__main__.py",
            ]
            for path in potential_paths:
                if path.exists():
                    agent_entrypoint = str(path)
                    logger.info(f"Discovered agent entrypoint: {agent_entrypoint}")
                    break

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
    if agent_port == TEST_HARNESS_PORT_START:
        agent_port = context.get("agent_port", TEST_HARNESS_PORT_START)

    # Check registry for agent info
    agents = state.get("agents", [])
    for agent in agents:
        if hasattr(agent, "name") and agent.name == experiment_plan.agent_name:
            agent_endpoint = agent_endpoint or getattr(agent, "endpoint", None)
            agent_entrypoint = agent_entrypoint or getattr(agent, "entrypoint", None)
        elif isinstance(agent, dict) and agent.get("name") == experiment_plan.agent_name:
            agent_endpoint = agent_endpoint or agent.get("endpoint")
            agent_entrypoint = agent_entrypoint or agent.get("entrypoint")

    # Find an available port before spawning the agent
    # Use TEST_HARNESS port range (9500+) to avoid conflicts with backend (8000) and production agents (8080+)
    agent_port = await find_available_port_async(
        start_port=agent_port,
        end_port=TEST_HARNESS_PORT_END
    )
    logger.info(f"Selected available test harness port for agent: {agent_port}")

    # Determine execution strategy
    if agent_entrypoint and not agent_endpoint:
        # NEW: Use test script execution flow
        # Spawn the agent, run generated tests, capture output
        logger.info(f"Using test script execution for entrypoint: {agent_entrypoint}")
        result = await harness.execute_with_test_script(
            experiment_plan=experiment_plan,
            agent_entrypoint=agent_entrypoint,
            port=agent_port,
            iteration=iteration,
            task_id=state.get("task_id"),
        )
        return result

    elif agent_endpoint:
        # Traditional HTTP exercise against running agent
        logger.info(f"Using HTTP exercise against endpoint: {agent_endpoint}")
        result = await harness.execute_experiment(
            experiment_plan=experiment_plan,
            agent_endpoint=agent_endpoint,
            agent_entrypoint=agent_entrypoint,
            iteration=iteration,
            task_id=state.get("task_id"),
        )
        return result

    else:
        # No endpoint or entrypoint - create failed result
        logger.warning("No agent endpoint or entrypoint available for exercise")
        from ..state.execution_schemas import ExecutionResult, ExecutionStatus
        from ..runtime.time_utils import utcnow

        result = ExecutionResult(
            iteration=iteration,
            task_id=state.get("task_id"),
            status=ExecutionStatus.FAILED,
            started_at=utcnow(),
            build_output=build_output,
            all_errors=["No agent endpoint or entrypoint available for exercise"],
        )
        # Don't call finalize() here - it would clear all_errors since there are no runs
        result.completed_at = utcnow()
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
