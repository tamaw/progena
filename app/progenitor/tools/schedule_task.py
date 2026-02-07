"""
Schedule Task - Schedule a task to run at a specific time.

Handles the full flow: LLM parses the user request, extracts time/instruction,
and creates a ScheduleEntry via the scheduler.
"""

import json
import logging

from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


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
        system_prompt = (
            "You are a scheduling assistant. Extract and format a scheduled task "
            "from the user's request.\n\n"
            "Return JSON with:\n"
            "{\n"
            '    "time_expression": "<when to run, e.g. \'in 5 minutes\', \'at 9am\', \'every day at 10am\'>",\n'
            '    "instruction": "<clear instruction for what to do at that time>",\n'
            '    "description": "<brief human-readable description of the task>"\n'
            "}\n\n"
            "Examples:\n"
            '- "remind me to call John in 30 minutes" -> time: "in 30 minutes", '
            'instruction: "Remind the user to call John", description: "Reminder: Call John"\n'
            '- "say hello world at 9am" -> time: "at 9am", '
            'instruction: "Say \'Hello World!\' to the user", description: "Say hello world"\n'
            '- "every day at 5pm tell me a joke" -> time: "every day at 5pm", '
            'instruction: "Tell the user a joke", description: "Daily joke"'
        )

        messages = [{"role": "user", "content": f"Schedule request: {user_input}"}]

        try:
            response = await context.llm_client.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.3,
                response_format={"type": "json_object"},
            )

            parsed = json.loads(response)

            time_expr = parsed.get("time_expression", "")
            instruction = parsed.get("instruction", user_input)
            description = parsed.get("description", f"Scheduled: {user_input[:50]}")

            if not time_expr:
                return ToolResult.fail(
                    "Could not extract time from your request. "
                    "Please specify when (e.g., 'in 5 minutes', 'at 9am')."
                )

            # Add the scheduled task
            result, entry_data = await add_scheduled_task(
                user_request=user_input,
                time_expression=time_expr,
                instruction=instruction,
                description=description,
            )

            if not result.success:
                return ToolResult.fail(result.message)

            tool_data = {
                "success": True,
                "schedule_id": result.schedule_id,
                "description": description,
                "instruction": instruction,
                "next_run": result.next_run,
                "recurring": "repeat_minutes" in entry_data if entry_data else False,
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

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse schedule LLM response: %s", e)
            return ToolResult.fail(f"Failed to process schedule request: {e}")
        except Exception as e:
            logger.error("Error executing schedule task: %s", e, exc_info=True)
            return ToolResult.fail(f"Error scheduling task: {e}")
