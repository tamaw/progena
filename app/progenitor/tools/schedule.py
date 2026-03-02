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
from ..runtime.time_utils import utcnow
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

    if not parsed or not parsed.get("cron_expression"):
        return ScheduleResult(
            success=False,
            message=f"Could not parse time expression: {time_expression}",
        ), None

    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
    task_message = instruction.strip()
    if not task_message:
        task_message = f"Scheduled: {user_request[:100]}"
    if description:
        task_message = f"{description.strip()} — {task_message}"
    created_at = utcnow().isoformat()
    next_run = scheduler.next_run_for_entry(
        {
            "cron_expression": parsed["cron_expression"],
            "last_run": None,
            "created_at": created_at,
        }
    )

    # Build the simplified schedule entry
    entry_data = {
        "id": schedule_id,
        "message": task_message,
        "user_request": user_request,
        "cron_expression": parsed["cron_expression"],
        "last_run": None,
        "created_at": created_at,
    }

    logger.info(
        "Created schedule entry %s: %s (%s)",
        schedule_id,
        task_message,
        parsed["cron_expression"],
    )

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Scheduled task created: {task_message}",
        next_run=next_run.isoformat() if next_run else None,
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
    scheduler = Scheduler()

    for entry in schedule:
        entry_dict = entry if isinstance(entry, dict) else entry.model_dump()

        status = "scheduled"
        next_run_dt = scheduler.next_run_for_entry(entry_dict)
        next_run = next_run_dt.isoformat() if next_run_dt else "Unknown"
        cron_expression = entry_dict.get("cron_expression")

        result.append({
            "id": entry_dict.get("id"),
            "message": entry_dict.get("message"),
            "status": status,
            "next_run": str(next_run),
            "cron_expression": cron_expression,
            "last_run": entry_dict.get("last_run"),
            "created_at": entry_dict.get("created_at"),
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
        },
        "required": ["user_request", "time_expression", "instruction"],
    },
}
