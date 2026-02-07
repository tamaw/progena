"""
Schedule Task - Schedule a task to run at a specific time.
"""

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

    async def execute(self, context: ToolContext) -> ToolResult:
        # This tool requires special handling with user input
        # It's typically executed via execute_schedule_task_inline
        return ToolResult.fail("schedule_task requires user input - use execute_schedule_task_inline")
