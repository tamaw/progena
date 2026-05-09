"""
Schedule Management Tool - Allows adding and managing scheduled tasks.

When a user requests something to be scheduled (e.g., "say hello at 9am"),
this tool processes the request into an instruction prompt that will be
executed at the scheduled time as if it were a new user request.
"""

import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..schedule import (
    delete_scheduled_task,
    list_scheduled_tasks,
    pause_scheduled_task,
    resume_scheduled_task,
    update_scheduled_task,
)
from .protocol import BaseSystemTool, ToolContext, ToolResult, build_tool_spec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
_SCHEDULE_ID_PATTERN = re.compile(r"\b(sched_[A-Za-z0-9]+)\b")


class ScheduleRequest(BaseModel):
    """Request to add a scheduled task."""

    user_request: str = Field(description="The original user request (e.g., 'say hello at 9am')")
    instruction: str = Field(
        description="Processed instruction for the agent to execute at the scheduled time"
    )
    cron_expression: str | None = Field(
        default=None,
        description="Cron expression for recurring tasks (e.g. '0 9 * * *')",
    )
    scheduled_at: str | None = Field(
        default=None,
        description="ISO8601 datetime for one-off tasks (e.g. '2026-06-01T09:00:00+00:00')",
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

Use this tool when the user asks to schedule work. Convert the user's time
request into explicit structured fields before calling the tool:
- Use scheduled_at for one-off tasks, as an ISO8601 datetime.
- Use cron_expression for recurring tasks.

The tool takes:
- user_request: The original user request
- instruction: A processed instruction that will run at the scheduled time as if the user sent it
- scheduled_at: One-off ISO8601 datetime, mutually exclusive with cron_expression
- cron_expression: Recurring cron expression, mutually exclusive with scheduled_at

Example: If user says "say hello world every day at 9am", create:
- cron_expression: "0 9 * * *"
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
