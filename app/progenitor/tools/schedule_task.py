"""Schedule Task - create schedule entries from explicit tool-call fields."""

import logging

from pydantic import BaseModel

from ..schedule import Scheduler
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ScheduleTaskTool(BaseSystemTool):
    """Schedule a task to run at a specific time."""

    @property
    def args_model(self) -> type[BaseModel]:
        from .schedule import ScheduleRequest

        return ScheduleRequest

    @property
    def name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return "Schedule a task to run at a specific time"

    @property
    def keywords(self) -> list[str]:
        return [
            "remind",
            "schedule",
            "set a reminder",
            "notify me",
            "alert me",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from ..schedule import add_scheduled_task

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        user_input = str(args.get("user_request") or context.user_input or "").strip()
        instruction = str(args.get("instruction") or "").strip()
        cron_expression = str(args.get("cron_expression") or "").strip()
        scheduled_at = str(args.get("scheduled_at") or "").strip()
        description = str(args.get("description") or "").strip()
        if not user_input:
            return ToolResult.fail("No user input provided for scheduling.")
        if not instruction:
            return ToolResult.fail("Missing 'instruction'")
        if bool(cron_expression) == bool(scheduled_at):
            return ToolResult.fail("Provide exactly one of 'cron_expression' or 'scheduled_at'")

        try:
            if not description:
                description = instruction[:60].strip() or f"Scheduled: {user_input[:50]}"

            result, entry_data = await add_scheduled_task(
                user_request=user_input,
                instruction=instruction,
                description=description,
                cron_expression=cron_expression,
                scheduled_at=scheduled_at,
            )

            if not result.success:
                return ToolResult.fail(result.message)

            scheduler = Scheduler()
            tool_data = {
                "success": True,
                "schedule_id": result.schedule_id,
                "message": entry_data.get("message") if entry_data else instruction,
                "next_run": result.next_run,
                "recurring": (
                    False
                    if entry_data and entry_data.get("scheduled_at")
                    else (
                        not scheduler.is_one_shot_expression(
                            str(entry_data.get("cron_expression", ""))
                        )
                        if entry_data
                        else True
                    )
                ),
            }

            # Build state_updates so the chat node can merge the new
            # schedule entry into graph state without special-case code.
            state_updates = None
            if entry_data:
                from ..state.models import ScheduleEntry

                new_entry = ScheduleEntry(**entry_data)
                current_schedule = (
                    list(context.state.get("schedule", []))
                    if isinstance(context.state, dict)
                    else []
                )
                state_updates = {"schedule": current_schedule + [new_entry]}

                # Persist
                if context.state_manager:
                    await context.state_manager.add_schedule_entry(new_entry)
                    logger.info(
                        "Persisted schedule entry %s",
                        entry_data.get("id"),
                    )
                    from .schedule import emit_schedule_changed_event

                    await emit_schedule_changed_event(context, str(entry_data.get("id") or ""))

            return ToolResult.ok(tool_data, state_updates=state_updates)
        except Exception as e:
            logger.error("Error executing schedule task: %s", e, exc_info=True)
            return ToolResult.fail(f"Error scheduling task: {e}")
