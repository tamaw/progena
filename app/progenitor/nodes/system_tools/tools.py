"""
System Tool Implementations.

Concrete implementations of system tools that can be executed inline.
"""

import logging
from pathlib import Path
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult
from ...runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class ListToolsTool(BaseSystemTool):
    """List all available tools including system tools, agents, and MCP tools."""

    @property
    def name(self) -> str:
        return "list_tools"

    @property
    def description(self) -> str:
        return "List all available tools and their capabilities"

    async def execute(self, context: ToolContext) -> ToolResult:
        from .registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]

        # Get registered MCP tools from registry
        mcp_tools = context.tool_registry.get_tools()
        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in mcp_tools
        ]

        return ToolResult.ok({
            "system_tools": system_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(mcp_tool_entries),
        })


class RediscoverAgentsTool(BaseSystemTool):
    """Scan the agents folder, analyze with LLM, and update registry.json."""

    @property
    def name(self) -> str:
        return "rediscover_agents"

    @property
    def description(self) -> str:
        return "Scan the agents folder, analyze with LLM, and update registry.json"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...tools.agent_discovery import discover_agents

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent.parent
        agents_dir = project_root / "app" / "agents"

        logger.info("Executing system tool: rediscover_agents")

        result = await discover_agents(
            agents_dir=agents_dir,
            llm_client=context.llm_client,
            registry=context.tool_registry,
        )

        return ToolResult.ok(result)


class ListAgentsTool(BaseSystemTool):
    """List all registered agents with their status."""

    @property
    def name(self) -> str:
        return "list_agents"

    @property
    def description(self) -> str:
        return "List all registered agents with their status"

    async def execute(self, context: ToolContext) -> ToolResult:
        agents = context.tool_registry.get_agents()
        all_agents = [
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
        ]

        running = [a for a in agents if a.get("status") == "running"]

        return ToolResult.ok({
            "agents": all_agents,
            "running_count": len(running),
            "total_count": len(all_agents),
        })


class BuildStatusTool(BaseSystemTool):
    """Check the current build status, progress, and messages."""

    @property
    def name(self) -> str:
        return "build_status"

    @property
    def description(self) -> str:
        return "Check the current build status, progress, and messages"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...runtime.background_build import get_background_runner

        runner = get_background_runner()

        if runner:
            build_result = runner.get_build_status()

            if build_result:
                result = {
                    "build_in_progress": build_result.status.value in ("pending", "running"),
                    "build_id": build_result.build_id,
                    "status": build_result.status.value,
                    "task_description": build_result.task_description,
                    "started_at": build_result.started_at.isoformat() if build_result.started_at else None,
                    "completed_at": build_result.completed_at.isoformat() if build_result.completed_at else None,
                    "iterations": build_result.iterations,
                    "verification_score": build_result.verification_score,
                    "output_summary": build_result.output_summary,
                    "error_message": build_result.error_message,
                    "agent_name": build_result.agent_name,
                    "success_criteria_met": build_result.success_criteria_met,
                    "success_criteria_unmet": build_result.success_criteria_unmet,
                }

                # Get recent builds
                recent_builds = runner.get_recent_builds(limit=3)
                if len(recent_builds) > 1:
                    result["recent_builds"] = [
                        {
                            "build_id": b.build_id,
                            "status": b.status.value,
                            "task_description": b.task_description[:50] + "..." if len(b.task_description) > 50 else b.task_description,
                        }
                        for b in recent_builds[:-1]
                    ]

                return ToolResult.ok(result)

        # Read from persisted state files via state_manager
        persisted_state = None
        if context.state_manager:
            try:
                persisted_state = await context.state_manager.get_state()
            except Exception as e:
                logger.warning("Failed to read persisted state: %s", e)

        # Merge in-memory state with persisted state
        effective_state: dict[str, Any] = {}
        if persisted_state:
            effective_state.update(persisted_state)
        if context.state:
            effective_state.update(context.state)

        if not effective_state:
            return ToolResult.ok({
                "build_in_progress": False,
                "message": "No build in progress and no recent builds found.",
            })

        build_in_progress = effective_state.get("build_in_progress", False)
        build_phase = effective_state.get("build_phase")
        build_task = effective_state.get("build_task_description")
        build_started = effective_state.get("build_started_at")
        build_id = effective_state.get("build_id")
        current_iteration = effective_state.get("iteration", {}).get("current", 0)
        verification_score = effective_state.get("verification", {}).get("current_score", 0.0)
        build_messages = effective_state.get("build_messages", [])
        ideal_state = effective_state.get("ideal_state")

        recent_messages = build_messages[-5:] if build_messages else []

        result = {
            "build_in_progress": build_in_progress,
            "build_id": build_id,
            "phase": build_phase,
            "task_description": build_task,
            "started_at": build_started,
            "current_iteration": current_iteration,
            "verification_score": verification_score,
            "message_count": len(build_messages),
            "recent_messages": recent_messages,
        }

        if ideal_state and isinstance(ideal_state, dict):
            result["target_score"] = ideal_state.get("stop_conditions", [{}])[0].get("threshold", 0.8)
            result["success_criteria"] = [
                c.get("description") for c in ideal_state.get("success_criteria", [])
            ]

        return ToolResult.ok(result)


class CurrentTimeTool(BaseSystemTool):
    """Return the current UTC time."""

    @property
    def name(self) -> str:
        return "current_time"

    @property
    def description(self) -> str:
        return "Return the current UTC time"

    async def execute(self, context: ToolContext) -> ToolResult:
        return ToolResult.ok({
            "utc_time": utcnow_isoformat(),
            "timezone": "UTC",
        })


class ListScheduleTool(BaseSystemTool):
    """List all scheduled tasks and reminders."""

    @property
    def name(self) -> str:
        return "list_schedule"

    @property
    def description(self) -> str:
        return "List all scheduled tasks and reminders"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state is None:
            return ToolResult.ok({
                "scheduled_tasks": [],
                "total_count": 0,
                "message": "No state available - schedule information unavailable",
            })

        schedule = context.state.get("schedule", [])

        # Convert schedule entries to readable format
        tasks = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
            elif isinstance(item, dict):
                item_dict = item
            else:
                continue

            tasks.append({
                "id": item_dict.get("id"),
                "description": item_dict.get("description", "No description"),
                "cron_expression": item_dict.get("cron_expression"),
                "next_run": item_dict.get("next_run"),
                "action_type": item_dict.get("action_type"),
                "enabled": item_dict.get("enabled", True),
            })

        return ToolResult.ok({
            "scheduled_tasks": tasks,
            "total_count": len(tasks),
        })


class SelfReflectTool(BaseSystemTool):
    """Introspect own source code to understand how Progenitor works."""

    @property
    def name(self) -> str:
        return "self_reflect"

    @property
    def description(self) -> str:
        return "Introspect own source code to understand how Progenitor works"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...tools.self_reflect import SelfReflection

        logger.info("Executing system tool: self_reflect")
        reflection = SelfReflection(llm_client=context.llm_client)

        # For inline execution, return module listing
        modules = reflection.list_modules()

        return ToolResult.ok({
            "description": "Progenitor self-reflection - exploring own source code",
            "root_path": str(reflection.root),
            "modules": [
                {
                    "name": m.name,
                    "path": m.path,
                    "is_directory": m.is_directory,
                    "description": m.description,
                }
                for m in modules
            ],
            "module_count": len([m for m in modules if m.is_directory]),
            "file_count": len([m for m in modules if not m.is_directory]),
            "tip": "Use follow-up questions to explore specific modules or search for code patterns.",
        })


class ScheduleTaskTool(BaseSystemTool):
    """Schedule a task to run at a specific time."""

    @property
    def name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return "Schedule a task to run at a specific time"

    async def execute(self, context: ToolContext) -> ToolResult:
        # This tool requires special handling with user input
        # It's typically executed via execute_schedule_task_inline
        return ToolResult.fail("schedule_task requires user input - use execute_schedule_task_inline")
