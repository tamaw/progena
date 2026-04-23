"""
List Schedule - List all scheduled tasks and reminders.
"""

import logging

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class ListScheduleTool(BaseSystemTool):
    """List all scheduled tasks and reminders."""

    @property
    def name(self) -> str:
        return "list_schedule"

    @property
    def description(self) -> str:
        return "List all scheduled tasks and reminders"

    @property
    def keywords(self) -> list[str]:
        return [
            "what is your schedule",
            "what's your schedule",
            "whats your schedule",
            "show schedule",
            "show my schedule",
            "show the schedule",
            "list schedule",
            "list schedules",
            "list scheduled",
            "view schedule",
            "view schedules",
            "scheduled tasks",
            "what is scheduled",
            "what's scheduled",
            "whats scheduled",
            "any scheduled",
            "pending schedule",
            "upcoming tasks",
            "upcoming schedule",
            "my reminders",
            "show reminders",
            "list reminders",
            "what reminders",
        ]

    @property
    def deepagent_requires_state(self) -> bool:
        return True

    async def execute(self, context: ToolContext) -> ToolResult:
        schedule = []
        if context.state_manager is not None and hasattr(context.state_manager, "get_schedule"):
            schedule = await context.state_manager.get_schedule()
        elif context.state is not None:
            schedule = context.state.get("schedule", [])
        else:
            return ToolResult.ok(
                {
                    "scheduled_tasks": [],
                    "total_count": 0,
                    "message": "No state available - schedule information unavailable",
                }
            )

        # Convert schedule entries to readable format
        tasks = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
            elif isinstance(item, dict):
                item_dict = item
            else:
                continue

            tasks.append(
                {
                    "id": item_dict.get("id"),
                    "message": item_dict.get("message", "No message"),
                    "cron_expression": item_dict.get("cron_expression"),
                    "last_run": item_dict.get("last_run"),
                    "created_at": item_dict.get("created_at"),
                }
            )

        return ToolResult.ok(
            {
                "scheduled_tasks": tasks,
                "total_count": len(tasks),
            }
        )
