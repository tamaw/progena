"""
New Conversation - Start a new conversation by resetting run_id and clearing session state.
"""

import logging

from ..state.models import build_new_conversation_state
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class NewConversationTool(BaseSystemTool):
    """Start a new conversation by resetting run_id and clearing session state."""

    @property
    def name(self) -> str:
        return "new_conversation"

    @property
    def description(self) -> str:
        return "Start a new conversation (fresh run_id, clears server-side context/history)"

    @property
    def keywords(self) -> list[str]:
        return [
            "new conversation",
            "start a new conversation",
            "start new conversation",
            "reset conversation",
            "clear conversation",
            "clear chat",
            "reset chat",
            "fresh run",
            "new run id",
            "new run",
            "start over",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state is None:
            return ToolResult.fail("No in-memory state available.")

        previous_state = context.state
        reset_state, reset_result = build_new_conversation_state(previous_state)

        # Update in-memory state directly (will be persisted by checkpoint)
        if context.state is not None:
            context.state.update(reset_state)  # type: ignore[typeddict-item]

        logger.info(
            "New conversation started (previous_run_id=%s, new_run_id=%s, messages_cleared=%d)",
            reset_result["previous_run_id"],
            reset_result["new_run_id"],
            reset_result["messages_cleared"],
        )

        # Return state_updates so the calling node can merge them properly
        return ToolResult.ok(reset_result, state_updates=reset_state)
