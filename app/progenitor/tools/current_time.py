"""
Current Time - Return the current UTC time.
"""

import logging

from ..utils import utcnow_isoformat
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class CurrentTimeTool(BaseSystemTool):
    """Return the current UTC time."""

    @property
    def name(self) -> str:
        return "current_time"

    @property
    def description(self) -> str:
        return "Return the current UTC time"

    @property
    def keywords(self) -> list[str]:
        return [
            "current time",
            "utc time",
            "time now",
            "what time is it",
            "tell me the time",
            "tell me the current time",
            "what is the time",
            "whats the time",
            "what's the time",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        _ = context
        return ToolResult.ok(
            {
                "utc_time": utcnow_isoformat(),
                "timezone": "UTC",
            }
        )
