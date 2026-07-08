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

    async def execute(self, context: ToolContext) -> ToolResult:
        _ = context
        return ToolResult.ok(
            {
                "utc_time": utcnow_isoformat(),
                "timezone": "UTC",
            }
        )
