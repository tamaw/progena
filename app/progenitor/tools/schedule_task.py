"""
Schedule Task - Schedule a task to run at a specific time.

Handles the full flow: LLM parses the user request, extracts time/instruction,
and creates a ScheduleEntry via the scheduler.
"""

import logging
from inspect import iscoroutinefunction

from pydantic import BaseModel

from ..llm.prompts import PromptTemplates
from ..runtime.scheduler import Scheduler
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


class _ScheduleExtractionResponse(BaseModel):
    time_expression: str | None = None
    instruction: str | None = None
    description: str | None = None


class ScheduleTaskTool(BaseSystemTool):
    """Schedule a task to run at a specific time."""

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

    @property
    def trigger_patterns(self) -> list[str]:
        return [
            # Time indicator + time pattern (e.g. "at 9am", "every day at 10:30pm")
            r"(?:at|every|in)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)",
            # "in X minutes/hours"
            r"\bin\s+\d+\s+(?:minute|hour|day)s?\b",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from .schedule import add_scheduled_task

        user_input = context.user_input
        if not user_input:
            return ToolResult.fail("No user input provided for scheduling.")

        # Use LLM to extract and process schedule parameters
        system_prompt = _prompts.get("progenitor/schedule_task_extraction_system.md")

        messages = [{"role": "user", "content": f"Schedule request: {user_input}"}]

        try:
            chat_structured = getattr(context.llm_client, "chat_structured", None)
            if not (callable(chat_structured) and iscoroutinefunction(chat_structured)):
                return ToolResult.fail(
                    "Structured scheduling is required, but chat_structured is unavailable. "
                    "Please configure the Azure structured output path."
                )

            structured = await chat_structured(
                messages=messages,
                response_model=_ScheduleExtractionResponse,
                schema_name="schedule_task_extraction",
                system_prompt=system_prompt,
                temperature=0.3,
                tools=[],
            )

            time_expr = (structured.time_expression or "").strip()
            instruction = (structured.instruction or "").strip()
            description = (structured.description or "").strip()

            if not time_expr:
                return ToolResult.fail(
                    "Could not extract time from your request. "
                    "Please specify when (e.g., 'in 5 minutes', 'at 9am')."
                )
            if not instruction:
                return ToolResult.fail(
                    "Could not extract instruction from your request. "
                    "Please restate what should happen at that time."
                )
            if not description:
                description = instruction[:60].strip() or f"Scheduled: {user_input[:50]}"

            # Add the scheduled task
            result, entry_data = await add_scheduled_task(
                user_request=user_input,
                time_expression=time_expr,
                instruction=instruction,
                description=description,
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
                    not scheduler.is_one_shot_expression(str(entry_data.get("cron_expression", "")))
                    if entry_data
                    else True
                ),
            }

            # Build state_updates so the decide node can merge the new
            # schedule entry into graph state without special-case code.
            state_updates = None
            if entry_data:
                from ..state.models import ScheduleEntry

                new_entry = ScheduleEntry(**entry_data)
                current_schedule = (
                    list(context.state.get("schedule", []))
                    if context.state
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

            return ToolResult.ok(tool_data, state_updates=state_updates)
        except Exception as e:
            logger.error("Error executing schedule task: %s", e, exc_info=True)
            return ToolResult.fail(f"Error scheduling task: {e}")
