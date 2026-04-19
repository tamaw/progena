"""
Schedule Management Tool - Allows adding and managing scheduled tasks.

When a user requests something to be scheduled (e.g., "say hello at 9am"),
this tool processes the request into an instruction prompt that will be
executed at the scheduled time as if it were a new user request.
"""

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..runtime.scheduler import Scheduler
from ..runtime.time_utils import utcnow
from .protocol import BaseSystemTool, ToolContext, ToolResult, build_tool_spec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
_SCHEDULE_ID_PATTERN = re.compile(r"\b(sched_[A-Za-z0-9]+)\b")


class ScheduleRequest(BaseModel):
    """Request to add a scheduled task."""

    user_request: str = Field(description="The original user request (e.g., 'say hello at 9am')")
    time_expression: str = Field(
        description="Time expression (e.g., 'at 9am', 'every day at 9am', 'in 30 minutes')"
    )
    instruction: str = Field(
        description="Processed instruction for the agent to execute at the scheduled time"
    )
    description: str | None = Field(
        default=None, description="Human-readable description of the scheduled task"
    )


class DeleteScheduleArgs(BaseModel):
    """Arguments for deleting a scheduled task."""

    schedule_id: str = Field(
        description="The ID of the scheduled task to delete (e.g. 'sched_abc123')"
    )


class UpdateScheduleArgs(BaseModel):
    """Arguments for updating a scheduled task."""

    schedule_id: str = Field(
        description="The ID of the scheduled task to update (e.g. 'sched_abc123')"
    )
    message: str | None = Field(
        default=None,
        description="New task message or instruction text",
    )
    cron_expression: str | None = Field(
        default=None,
        description="New cron expression for recurring tasks (e.g. '0 9 * * *')",
    )
    scheduled_at: str | None = Field(
        default=None,
        description="New ISO8601 datetime for one-off tasks (e.g. '2026-06-01T09:00:00+00:00')",
    )


class PauseScheduleArgs(BaseModel):
    """Arguments for pausing a scheduled task."""

    schedule_id: str = Field(
        description="The ID of the scheduled task to pause (e.g. 'sched_abc123')"
    )


class ResumeScheduleArgs(BaseModel):
    """Arguments for resuming a scheduled task."""

    schedule_id: str = Field(
        description="The ID of the scheduled task to resume (e.g. 'sched_abc123')"
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

    Supports either a cron-like/natural-language time expression or an explicit
    ISO8601 timestamp (scheduled_at). One-off timestamps are stored as
    'scheduled_at' on the entry to avoid imprecise cron fallbacks.
    """
    scheduler = Scheduler()

    # Parse the time expression
    parsed = scheduler.parse_time_expression(time_expression)

    # Require either a cron_expression (recurring) or scheduled_at (one-off)
    if not parsed or not (parsed.get("cron_expression") or parsed.get("scheduled_at")):
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

    # Prepare entry dict depending on whether this is a one-off or recurring
    entry = {
        "id": schedule_id,
        "message": task_message,
        "user_request": user_request,
        "last_run": None,
        "created_at": created_at,
    }

    if parsed.get("scheduled_at"):
        entry["scheduled_at"] = parsed["scheduled_at"]
        next_run = scheduler.next_run_for_entry(entry)
        logger.info(
            "Created one-off schedule entry %s: %s (at %s)",
            schedule_id,
            task_message,
            parsed["scheduled_at"],
        )
    else:
        entry["cron_expression"] = parsed["cron_expression"]
        next_run = scheduler.next_run_for_entry(
            {
                "cron_expression": parsed["cron_expression"],
                "last_run": None,
                "created_at": created_at,
            }
        )
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
    ), entry


async def delete_scheduled_task(
    schedule_id: str,
    state_manager: Any | None = None,
) -> ScheduleResult:
    """
    Delete a scheduled task by ID, removing it permanently from the schedule.

    Unlike ``remove_scheduled_task`` (which only signals intent), this function
    calls the state manager directly to persist the removal.

    Args:
        schedule_id: The ID of the task to delete (e.g. "sched_abc123").
        state_manager: Optional StateManager instance.  When provided the entry
            is removed from ``schedule.json``; otherwise only the ScheduleResult
            is returned (useful for pure-logic tests).

    Returns:
        ScheduleResult with success=True if the entry was found and deleted, or
        success=False with an explanatory message if the ID was not found.
    """
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(
            success=False,
            message="schedule_id is required",
        )

    if state_manager is not None:
        removed = await state_manager.remove_schedule_entry(schedule_id)
        if not removed:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Deleted schedule entry %s", schedule_id)

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Task {schedule_id} deleted",
    )


async def update_scheduled_task(
    schedule_id: str,
    updates: dict[str, Any],
    state_manager: Any | None = None,
) -> ScheduleResult:
    """
    Update fields of an existing scheduled task.

    Allowed update keys include ``message``, ``cron_expression``,
    ``scheduled_at``, and ``user_request``.  The ``id``, ``created_at``, and
    ``last_run`` fields are intentionally excluded from the updates dict to
    preserve task identity and execution history.

    Args:
        schedule_id: The ID of the task to update.
        updates: Dict of fields to update (e.g. ``{"message": "new text",
            "cron_expression": "0 10 * * *"}``).
        state_manager: Optional StateManager instance for persistence.

    Returns:
        ScheduleResult with success=True when the entry was updated, or
        success=False with an explanatory message on failure.
    """
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(
            success=False,
            message="schedule_id is required",
        )
    if not updates:
        return ScheduleResult(
            success=False,
            schedule_id=schedule_id,
            message="updates dict must not be empty",
        )

    # Reject protected fields to prevent accidental corruption.
    protected = {"id", "created_at", "last_run"}
    bad_keys = protected & set(updates.keys())
    if bad_keys:
        return ScheduleResult(
            success=False,
            schedule_id=schedule_id,
            message=f"Cannot update protected field(s): {', '.join(sorted(bad_keys))}",
        )

    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, updates)
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Updated schedule entry %s: %s", schedule_id, list(updates.keys()))

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Task {schedule_id} updated",
    )


async def pause_scheduled_task(
    schedule_id: str,
    state_manager: Any | None = None,
) -> ScheduleResult:
    """
    Pause a scheduled task so it is skipped by the scheduler without being deleted.

    Sets ``paused=True`` on the ScheduleEntry.  The entry is preserved in full
    and can be resumed at any time with ``resume_scheduled_task``.

    Args:
        schedule_id: The ID of the task to pause.
        state_manager: Optional StateManager instance for persistence.

    Returns:
        ScheduleResult with success=True when the entry was paused.
    """
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(
            success=False,
            message="schedule_id is required",
        )

    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, {"paused": True})
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Paused schedule entry %s", schedule_id)

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Task {schedule_id} paused",
    )


async def resume_scheduled_task(
    schedule_id: str,
    state_manager: Any | None = None,
) -> ScheduleResult:
    """
    Resume a previously paused scheduled task.

    Sets ``paused=False`` on the ScheduleEntry, re-enabling normal scheduler
    processing.

    Args:
        schedule_id: The ID of the task to resume.
        state_manager: Optional StateManager instance for persistence.

    Returns:
        ScheduleResult with success=True when the entry was resumed.
    """
    if not schedule_id or not schedule_id.strip():
        return ScheduleResult(
            success=False,
            message="schedule_id is required",
        )

    if state_manager is not None:
        updated = await state_manager.update_schedule_entry(schedule_id, {"paused": False})
        if not updated:
            return ScheduleResult(
                success=False,
                schedule_id=schedule_id,
                message=f"Task {schedule_id} not found",
            )
        logger.info("Resumed schedule entry %s", schedule_id)

    return ScheduleResult(
        success=True,
        schedule_id=schedule_id,
        message=f"Task {schedule_id} resumed",
    )


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

        result.append(
            {
                "id": entry_dict.get("id"),
                "message": entry_dict.get("message"),
                "status": status,
                "next_run": str(next_run),
                "cron_expression": cron_expression,
                "last_run": entry_dict.get("last_run"),
                "created_at": entry_dict.get("created_at"),
            }
        )

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
            schedule_dicts = [
                e.model_dump() if hasattr(e, "model_dump") else e for e in schedule_entries
            ]
        else:
            schedule_dicts = []

        listed = await list_scheduled_tasks(schedule_dicts)
        return ToolResult.ok(
            {
                "count": len(listed),
                "schedule": listed,
            }
        )


def _extract_schedule_id(args: dict[str, Any], user_input: str) -> str:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if schedule_id:
        return schedule_id

    request = str(args.get("request") or user_input or "").strip()
    match = _SCHEDULE_ID_PATTERN.search(request)
    return match.group(1) if match else ""


class DeleteScheduleTool(BaseSystemTool):
    """Delete a scheduled task from persistent schedule state."""

    @property
    def args_model(self) -> type[BaseModel]:
        return DeleteScheduleArgs

    @property
    def name(self) -> str:
        return "delete_schedule"

    @property
    def description(self) -> str:
        return "Permanently delete a scheduled task by its ID"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot delete schedule entries")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        schedule_id = _extract_schedule_id(args, context.user_input)
        if not schedule_id:
            return ToolResult.fail("Missing 'schedule_id'")

        result = await delete_scheduled_task(schedule_id, state_manager=context.state_manager)
        if not result.success:
            return ToolResult.fail(result.message)

        return ToolResult.ok(
            {
                "success": True,
                "schedule_id": schedule_id,
                "message": result.message,
            }
        )


class UpdateScheduleTool(BaseSystemTool):
    """Update an existing scheduled task."""

    @property
    def args_model(self) -> type[BaseModel]:
        return UpdateScheduleArgs

    @property
    def name(self) -> str:
        return "update_schedule"

    @property
    def description(self) -> str:
        return "Update fields of an existing scheduled task without recreating it"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot update schedule entries")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        schedule_id = _extract_schedule_id(args, context.user_input)
        if not schedule_id:
            return ToolResult.fail("Missing 'schedule_id'")

        updates = {
            key: value
            for key, value in args.items()
            if key in {"message", "cron_expression", "scheduled_at", "user_request"}
            and value is not None
        }
        if not updates:
            return ToolResult.fail("No schedule updates provided")

        result = await update_scheduled_task(
            schedule_id,
            updates,
            state_manager=context.state_manager,
        )
        if not result.success:
            return ToolResult.fail(result.message)

        return ToolResult.ok(
            {
                "success": True,
                "schedule_id": schedule_id,
                "message": result.message,
                "updates": updates,
            }
        )


class PauseScheduleTool(BaseSystemTool):
    """Pause a scheduled task without deleting it."""

    @property
    def args_model(self) -> type[BaseModel]:
        return PauseScheduleArgs

    @property
    def name(self) -> str:
        return "pause_schedule"

    @property
    def description(self) -> str:
        return "Pause a scheduled task so it is skipped without being deleted"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot pause schedule entries")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        schedule_id = _extract_schedule_id(args, context.user_input)
        if not schedule_id:
            return ToolResult.fail("Missing 'schedule_id'")

        result = await pause_scheduled_task(schedule_id, state_manager=context.state_manager)
        if not result.success:
            return ToolResult.fail(result.message)

        return ToolResult.ok(
            {
                "success": True,
                "schedule_id": schedule_id,
                "message": result.message,
            }
        )


class ResumeScheduleTool(BaseSystemTool):
    """Resume a previously paused scheduled task."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ResumeScheduleArgs

    @property
    def name(self) -> str:
        return "resume_schedule"

    @property
    def description(self) -> str:
        return "Resume a paused scheduled task"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot resume schedule entries")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        schedule_id = _extract_schedule_id(args, context.user_input)
        if not schedule_id:
            return ToolResult.fail("Missing 'schedule_id'")

        result = await resume_scheduled_task(schedule_id, state_manager=context.state_manager)
        if not result.success:
            return ToolResult.fail(result.message)

        return ToolResult.ok(
            {
                "success": True,
                "schedule_id": schedule_id,
                "message": result.message,
            }
        )


# Tool registration for use by decide/planner nodes
SCHEDULE_TOOL_SPEC = build_tool_spec(
    name="schedule_task",
    description="""Schedule a task to run at a specific time or recurring interval.

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
    args_model=ScheduleRequest,
)

# ------------------------------------------------------------------
# Lifecycle tool specs – orchestrator-callable, similar to SCHEDULE_TOOL_SPEC
# ------------------------------------------------------------------

DELETE_SCHEDULE_TOOL_SPEC = build_tool_spec(
    name="delete_schedule",
    description="""Permanently delete a scheduled task by its ID.

Use this tool when the user asks to:
- Remove, cancel, or delete a scheduled task or reminder
- Stop a recurring task permanently

The tool takes:
- schedule_id: The ID of the schedule entry to delete (e.g. "sched_abc123").
  Use list_schedule to find the ID if you don't have it.
""",
    args_model=DeleteScheduleArgs,
)

UPDATE_SCHEDULE_TOOL_SPEC = build_tool_spec(
    name="update_schedule",
    description="""Update fields of an existing scheduled task without recreating it.

Use this tool when the user asks to:
- Change the time or cron expression of an existing schedule
- Edit the message or description of a scheduled task
- Reschedule a task to a new time

Updatable fields: ``message``, ``cron_expression``, ``scheduled_at``,
``user_request``.  Protected fields (``id``, ``created_at``, ``last_run``)
cannot be changed.

The tool takes:
- schedule_id: The ID of the task to update.
- message: (optional) New task message / instruction.
- cron_expression: (optional) New cron expression for recurring tasks.
- scheduled_at: (optional) New ISO8601 one-off timestamp.
""",
    args_model=UpdateScheduleArgs,
)

PAUSE_SCHEDULE_TOOL_SPEC = build_tool_spec(
    name="pause_schedule",
    description="""Pause a scheduled task so it is skipped by the scheduler without being deleted.

Use this tool when the user asks to:
- Temporarily stop or suspend a scheduled reminder or task
- Pause a recurring task without losing its configuration

The task can be re-enabled later with resume_schedule.

The tool takes:
- schedule_id: The ID of the task to pause (e.g. "sched_abc123").
""",
    args_model=PauseScheduleArgs,
)

RESUME_SCHEDULE_TOOL_SPEC = build_tool_spec(
    name="resume_schedule",
    description="""Resume a previously paused scheduled task.

Use this tool when the user asks to:
- Re-enable or resume a paused reminder or task
- Restart a suspended scheduled task

The tool takes:
- schedule_id: The ID of the task to resume (e.g. "sched_abc123").
""",
    args_model=ResumeScheduleArgs,
)
