"""
Schedule Management Tool - Allows adding and managing scheduled tasks.

When a user requests something to be scheduled (e.g., "say hello at 9am"),
this tool processes the request into an instruction prompt that will be
executed at the scheduled time as if it were a new user request.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from ..state.models import ScheduleEntry, DecisionAction
from ..runtime.scheduler import Scheduler
from ..runtime.time_utils import utcnow

if TYPE_CHECKING:
    from ..llm.client import LLMClient

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
    description: Optional[str] = Field(
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
    schedule_id: Optional[str] = None
    message: str
    next_run: Optional[str] = None


async def add_scheduled_task(
    user_request: str,
    time_expression: str,
    instruction: str,
    description: Optional[str] = None,
    recurring: bool = False,
) -> tuple[ScheduleResult, Optional[dict]]:
    """
    Add a scheduled task that will execute an instruction at the specified time.

    Args:
        user_request: The original user request for context
        time_expression: Natural language time (e.g., "at 9am", "every day at 9am")
        instruction: The processed instruction to run at the scheduled time
        description: Human-readable description
        recurring: Whether this repeats

    Returns:
        Tuple of (ScheduleResult with the schedule ID and status, entry_data dict or None on failure)
    """
    scheduler = Scheduler()

    # Parse the time expression
    parsed = scheduler.parse_time_expression(time_expression)

    if not parsed:
        return ScheduleResult(
            success=False,
            message=f"Could not parse time expression: {time_expression}",
        ), None

    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
    task_description = description or f"Scheduled: {user_request[:100]}"

    # Build the schedule entry data
    entry_data = {
        "id": schedule_id,
        "description": task_description,
        "instruction": instruction,  # The key field - instruction to execute
        "user_request": user_request,  # Keep original for reference
        "enabled": True,
        "action_type": DecisionAction.ANSWER_DIRECTLY.value,  # Will be re-decided at runtime
        "action_config": {
            "original_request": user_request,
        },
    }

    # Add timing based on parsed expression
    if parsed.get("run_at"):
        entry_data["run_at"] = parsed["run_at"]
        next_run = parsed["run_at"]
    elif parsed.get("cron_expression"):
        entry_data["cron_expression"] = parsed["cron_expression"]
        # Calculate next run from cron
        next_run = parsed.get("description", "recurring")
    elif parsed.get("interval_minutes"):
        entry_data["interval_minutes"] = parsed["interval_minutes"]
        next_run = (utcnow() + timedelta(minutes=parsed["interval_minutes"])).isoformat()
    else:
        return ScheduleResult(
            success=False,
            message=f"No valid timing found in: {time_expression}",
        ), None

    logger.info(
        "Created schedule entry %s: %s -> %s",
        schedule_id,
        task_description,
        next_run,
    )

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Scheduled task created: {task_description}",
        next_run=str(next_run) if next_run else None,
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
    now = utcnow()

    for entry in schedule:
        entry_dict = entry if isinstance(entry, dict) else entry.model_dump()

        status = "enabled" if entry_dict.get("enabled", True) else "disabled"

        # Calculate next run
        next_run = None
        if entry_dict.get("run_at"):
            next_run = entry_dict["run_at"]
        elif entry_dict.get("cron_expression"):
            next_run = f"Recurring: {entry_dict['cron_expression']}"
        elif entry_dict.get("interval_minutes"):
            last_run = entry_dict.get("last_run")
            if last_run:
                next_run = f"Every {entry_dict['interval_minutes']} minutes"
            else:
                next_run = "Next cycle"

        result.append({
            "id": entry_dict.get("id"),
            "description": entry_dict.get("description"),
            "instruction": entry_dict.get("instruction", entry_dict.get("action_config", {}).get("instruction")),
            "status": status,
            "next_run": str(next_run) if next_run else "Unknown",
            "last_run": entry_dict.get("last_run"),
        })

    return result


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
