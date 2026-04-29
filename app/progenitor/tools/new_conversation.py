"""New Conversation - Start a new conversation by resetting the main thread ID."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from ..state.models import generate_thread_id
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class NewConversationArgs(BaseModel):
    request: str = Field(
        default="",
        description="Optional instruction accompanying the conversation reset.",
    )


class NewConversationTool(BaseSystemTool):
    """Start a new conversation by resetting thread_id and clearing session state."""

    @property
    def name(self) -> str:
        return "new_conversation"

    @property
    def description(self) -> str:
        return "Start a new conversation (fresh thread_id, clears server-side context/history)"

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
            "fresh thread",
            "new thread id",
            "start over",
        ]

    @property
    def args_model(self) -> type[BaseModel]:
        return NewConversationArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state is None:
            return ToolResult.fail("No in-memory state available.")

        previous_state = context.state

        previous_thread_id = (
            previous_state.get("thread_id") if isinstance(previous_state, dict) else None
        )
        previous_messages = (
            previous_state.get("messages", []) if isinstance(previous_state, dict) else []
        )
        messages_cleared = len(previous_messages or [])

        new_thread_id = generate_thread_id()

        reset_state: dict[str, Any] = {
            "thread_id": new_thread_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "last_checkpoint": None,
            "messages": [],
            "attachments": [],
            "tool_calls": [],
            "current_input": None,
            "pending_response": None,
        }

        # Update in-memory state directly (will be persisted by checkpoint)
        if context.state is not None:
            context.state.update(reset_state)  # type: ignore[typeddict-item]

        logger.info(
            "New conversation started (previous_thread_id=%s, new_thread_id=%s, messages_cleared=%d)",
            previous_thread_id,
            new_thread_id,
            messages_cleared,
        )

        # Return state_updates so the calling node can merge them properly
        return ToolResult.ok(
            {
                "reset": True,
                "thread_id": new_thread_id,
                "new_thread_id": new_thread_id,
                "previous_thread_id": previous_thread_id,
                "messages_cleared": messages_cleared,
            },
            state_updates=reset_state,
        )
