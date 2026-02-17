"""
Schedule Management Tool - Allows adding and managing scheduled tasks.

When a user requests something to be scheduled (e.g., "say hello at 9am"),
this tool processes the request into an instruction prompt that will be
executed at the scheduled time as if it were a new user request.
"""

import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..runtime.scheduler import Scheduler
from .protocol import BaseSystemTool, ToolContext, ToolResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ScheduleRequest(BaseModel):
    """Request to add a scheduled task."""

    user_request: str = Field(
        description="The original user request (e.g., 'say hello at 9am')"
    )
    time_expression: str = Field(
        description="Time expression (e.g., 'at 9am', 'every day at 9am', 'in 30 minutes')"
    )
    instruction: str = Field(
        description="Processed instruction for the agent to execute at the scheduled time"
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of the scheduled task"
    )
    recurring: bool = Field(
        default=False,
        description="Whether this is a recurring task"
    )


class ScheduleResult(BaseModel):
    """Result of a schedule operation."""

    success: bool
    schedule_id: str | None = None
    message: str
    next_run: str | None = None


async def add_scheduled_task(
    user_request: str,
    time_expression: str,
    instruction: str,
    description: str | None = None,
) -> tuple[ScheduleResult, dict | None]:
    """
    Add a scheduled task that will execute an instruction at the specified time.

    Args:
        user_request: The original user request for context
        time_expression: Natural language time (e.g., "at 9am", "in 30 minutes", "every day at 9am")
        instruction: The processed instruction to run at the scheduled time
        description: Human-readable description

    Returns:
        Tuple of (ScheduleResult with the schedule ID and status, entry_data dict or None on failure)
    """
    scheduler = Scheduler()

    # Parse the time expression
    parsed = scheduler.parse_time_expression(time_expression)

    if not parsed or not parsed.get("run_at"):
        return ScheduleResult(
            success=False,
            message=f"Could not parse time expression: {time_expression}",
        ), None

    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
    task_description = description or f"Scheduled: {user_request[:100]}"

    # Build the simplified schedule entry
    entry_data = {
        "id": schedule_id,
        "description": task_description,
        "instruction": instruction,
        "run_at": parsed["run_at"],
        "enabled": True,
    }

    # Add repeat_minutes if this is a recurring task
    if parsed.get("repeat_minutes"):
        entry_data["repeat_minutes"] = parsed["repeat_minutes"]

    logger.info(
        "Created schedule entry %s: %s -> %s%s",
        schedule_id,
        task_description,
        parsed["run_at"],
        f" (repeat every {parsed.get('repeat_minutes')} min)" if parsed.get("repeat_minutes") else "",
    )

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Scheduled task created: {task_description}",
        next_run=parsed["run_at"],
    ), entry_data


async def remove_scheduled_task(schedule_id: str) -> ScheduleResult:
    """
    Remove a scheduled task by ID.

    Note: The actual removal happens in the state manager. This function
    returns the ID to be removed.
    """
    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Task {schedule_id} marked for removal",
    )


async def list_scheduled_tasks(schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    List all scheduled tasks with their status.

    Args:
        schedule: Current schedule from state

    Returns:
        List of schedule entries with status info
    """
    result = []

    for entry in schedule:
        entry_dict = entry if isinstance(entry, dict) else entry.model_dump()

        # Determine status
        if not entry_dict.get("enabled", True):
            status = "disabled"
        elif entry_dict.get("last_run") and not entry_dict.get("repeat_minutes"):
            status = "completed"  # One-off that already ran
        else:
            status = "scheduled"

        # Format next run info
        run_at = entry_dict.get("run_at")
        repeat = entry_dict.get("repeat_minutes")
        if repeat:
            next_run = f"{run_at} (every {repeat} min)"
        elif run_at:
            next_run = run_at
        else:
            next_run = "Unknown"

        result.append({
            "id": entry_dict.get("id"),
            "description": entry_dict.get("description"),
            "instruction": entry_dict.get("instruction"),
            "status": status,
            "next_run": str(next_run),
            "last_run": entry_dict.get("last_run"),
            "repeat_minutes": repeat,
        })

    return result


class ScheduleTool(BaseSystemTool):
    """Expose schedule status and entries (read-only)."""

    @property
    def name(self) -> str:
        return "schedule"

    @property
    def description(self) -> str:
        return "List scheduled tasks and their next run time (read-only)"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager and hasattr(context.state_manager, "get_schedule"):
            schedule_entries = await context.state_manager.get_schedule()
            # state_manager may return Pydantic models
            schedule_dicts = [e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries]
        else:
            schedule_dicts = []

        listed = await list_scheduled_tasks(schedule_dicts)
        return ToolResult.ok({
            "count": len(listed),
            "schedule": listed,
        })


# Tool registration for use by decide/planner nodes
SCHEDULE_TOOL_SPEC = {
    "name": "schedule_task",
    "description": """Schedule a task to run at a specific time or recurring interval.

Use this tool when the user asks to:
- Do something at a specific time (e.g., "remind me at 9am", "say hello tomorrow")
- Set up recurring tasks (e.g., "check stock prices every day at 10am")
- Schedule future actions (e.g., "in 30 minutes, tell me a joke")

The tool takes:
- user_request: The original user request
- time_expression: The time/schedule (e.g., "at 9am", "every day at 9am", "in 30 minutes", "tomorrow")
- instruction: A processed instruction that will run at the scheduled time as if the user sent it

Example: If user says "say hello world at 9am", create:
- time_expression: "every day at 9am" (or "at 9am" for one-time)
- instruction: "Say 'Hello World!' to the user as they requested."
""",
    "parameters": {
        "type": "object",
        "properties": {
            "user_request": {
                "type": "string",
                "description": "The original user request",
            },
            "time_expression": {
                "type": "string",
                "description": "Time expression like 'at 9am', 'every day at 10am', 'in 30 minutes'",
            },
            "instruction": {
                "type": "string",
                "description": "Instruction for the agent to execute at the scheduled time",
            },
            "description": {
                "type": "string",
                "description": "Optional human-readable description",
            },
            "recurring": {
                "type": "boolean",
                "description": "Whether this is a recurring task (default: false)",
            },
        },
        "required": ["user_request", "time_expression", "instruction"],
    },
}
