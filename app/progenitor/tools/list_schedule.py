"""
List Schedule - List all scheduled tasks and reminders.
"""

import logging

from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

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
