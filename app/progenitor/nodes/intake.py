"""
Intake Node - Handles incoming user messages.

Appends user message to conversation log and updates context.
Also detects when a message is a response to a clarification request.
"""

from datetime import datetime
from typing import Any, Callable, Coroutine
import uuid

from ..state.models import ConversationMessage, ProgenitorState
from ..state.helpers import trim_conversation_history
from ..runtime.time_utils import utcnow


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the intake node function."""

    async def intake_node(state: ProgenitorState) -> ProgenitorState:
        """Process incoming user message.

        If we're awaiting clarification from the user, this message
        is treated as the clarification response and routed accordingly.
        """
        user_input = state.get("current_input")

        if not user_input:
            # No new input - just pass through
            return {}

        # Check if this is a clarification response
        awaiting_clarification = state.get("awaiting_clarification", False)
        clarification_updates = {}

        if awaiting_clarification:
            # This message is a response to our clarification request
            clarification_updates = {
                "clarification_response": user_input,
                # Keep awaiting_clarification True - observe node will clear it
                # after incorporating the response
            }

        # Create message record with metadata about clarification
        metadata = {}
        if awaiting_clarification:
            metadata["type"] = "clarification_response"
            metadata["clarification_context"] = state.get("clarification_context")

        message = ConversationMessage(
            id=str(uuid.uuid4()),
            role="user",
            content=user_input,
            timestamp=utcnow(),
            metadata=metadata,
        )

        # Log to conversation history (PRIMARY RULE: Log the human first!)
        await state_manager.log_conversation(message)

        # Update messages in state
        messages = list(state.get("messages", []))
        messages.append(message)

        # Trim conversation history to prevent unbounded growth (TD-008)
        messages = trim_conversation_history(messages)

        return {
            "messages": messages,
            "current_input": user_input,
            **clarification_updates,
        }

    return intake_node
