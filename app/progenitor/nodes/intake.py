"""
Intake Node - Handles incoming user messages.

Appends user message to conversation log and updates context.
Also detects when a message is a response to a clarification request.

Messages are stored in current_state.json (progenitor agent state).
"""

import uuid
from collections.abc import Callable, Coroutine
from typing import Any, cast

from ..runtime.time_utils import utcnow
from ..state.models import ConversationMessage, ProgenitorState

QUEUE_WAIT_THRESHOLD_SECONDS = 30.0


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the intake node function."""

    async def intake_node(state: ProgenitorState) -> ProgenitorState:
        """Process incoming user message.

        If we're awaiting clarification from the user, this message
        is treated as the clarification response and routed accordingly.

        Messages are persisted to current_state.json.
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
        metadata = dict(state.get("current_input_metadata") or {})
        if awaiting_clarification:
            metadata["type"] = "clarification_response"
            metadata["clarification_context"] = state.get("clarification_context")

        wait_secs = float(state.get("input_wait_seconds") or 0.0)
        pending_count = int(state.get("pending_messages_in_queue") or 0)
        if wait_secs >= QUEUE_WAIT_THRESHOLD_SECONDS:
            metadata["queued_wait_seconds"] = round(wait_secs, 1)
        if pending_count > 0:
            metadata["pending_messages_in_queue"] = pending_count

        role = state.get("current_input_role") or "user"

        message = ConversationMessage(
            id=str(uuid.uuid4()),
            role=role,
            content=user_input,
            timestamp=utcnow(),
            metadata=metadata,
        )

        # Log to conversation JSONL (PRIMARY RULE: Log the human first!)
        await state_manager.log_conversation(message)

        # Update messages in state
        messages = list(state.get("messages", []))
        messages.append(message)

        # Persist messages to current_state.json
        await state_manager.save_messages(messages)

        return cast(
            ProgenitorState,
            {
                "messages": messages,
                "current_input": user_input,
                "current_input_role": None,
                "current_input_metadata": None,
                **clarification_updates,
            },
        )

    return intake_node
