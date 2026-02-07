"""
New Conversation - Start a new conversation by resetting run_id and clearing session state.
"""

import logging
from typing import Any

from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult
from ..state.models import generate_run_id

logger = logging.getLogger(__name__)


class NewConversationTool(BaseSystemTool):
    """Start a new conversation by resetting run_id and clearing session state."""

    @property
    def name(self) -> str:
        return "new_conversation"

    @property
    def description(self) -> str:
        return "Start a new conversation (fresh run_id, clear chat history)"

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

        previous_run_id = previous_state.get("run_id") if isinstance(previous_state, dict) else None
        previous_messages = previous_state.get("messages", []) if isinstance(previous_state, dict) else []
        messages_cleared = len(previous_messages or [])

        new_run_id = generate_run_id()

        reset_state: dict[str, Any] = {
            "run_id": new_run_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "last_checkpoint": None,
            "messages": [],
            "attachments": [],
            "tool_calls": [],
            "current_input": None,
            "pending_response": None,
            "current_decision": None,
            "current_build_request": None,
        }

        # Update in-memory state directly (will be persisted by checkpoint)
        if context.state is not None:
            context.state.update(reset_state)  # type: ignore[arg-type]

        logger.info(
            "New conversation started (previous_run_id=%s, new_run_id=%s, messages_cleared=%d)",
            previous_run_id,
            new_run_id,
            messages_cleared,
        )

        # Return state_updates so the calling node can merge them properly
        return ToolResult.ok(
            {
                "reset": True,
                "run_id": new_run_id,
                "new_run_id": new_run_id,
                "previous_run_id": previous_run_id,
                "messages_cleared": messages_cleared,
            },
            state_updates=reset_state
        )
