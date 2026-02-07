"""
New Conversation - Start a new conversation by resetting run_id and clearing session state.
"""

import logging

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

    async def execute(self, context: ToolContext) -> ToolResult:
        if not context.state_manager and context.state is None:
            return ToolResult.fail("No state manager or in-memory state available.")

        new_run_id = generate_run_id()

        reset_state = {
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

        if context.state_manager:
            await context.state_manager.update_current_state(reset_state)

        if context.state is not None:
            context.state.update(reset_state)

        return ToolResult.ok({
            "reset": True,
            "run_id": new_run_id,
        })
