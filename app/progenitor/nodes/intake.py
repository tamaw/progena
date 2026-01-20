"""
Intake Node - Handles incoming user messages.

Appends user message to conversation log and updates context.
"""

from datetime import datetime
from typing import Any, Callable, Coroutine
import uuid

from ..state.models import ConversationMessage, ProgenitorState
from ..runtime.time_utils import utcnow


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the intake node function."""

    async def intake_node(state: ProgenitorState) -> ProgenitorState:
        """Process incoming user message."""
        user_input = state.get("current_input")

        if not user_input:
            # No new input - just pass through
            return {}

        # Create message record
        message = ConversationMessage(
            id=str(uuid.uuid4()),
            role="user",
            content=user_input,
            timestamp=utcnow(),
        )

        # Log to conversation history (PRIMARY RULE: Log the human first!)
        await state_manager.log_conversation(message)

        # Update messages in state
        messages = list(state.get("messages", []))
        messages.append(message)

        return {
            "messages": messages,
            "current_input": user_input,
        }

    return intake_node
