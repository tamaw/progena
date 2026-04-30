"""Bridge terminal A2A task records into conversation state."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from ..state.models import A2ATaskRecord, ConversationMessage
from .a2a_tasks import build_completion_message
from .time_utils import utcnow


class A2ACompletionBridge:
    """Persists terminal A2A updates into the active conversation."""

    def __init__(
        self,
        *,
        state_manager: Any,
        get_state: Callable[[], dict[str, Any]],
        graph_lock: asyncio.Lock,
    ) -> None:
        self._state_manager = state_manager
        self._get_state = get_state
        self._graph_lock = graph_lock

    async def record_completed_task(self, record: A2ATaskRecord) -> None:
        """Persist a terminal A2A task update into the active conversation state."""
        metadata = {
            "type": "a2a_task_completion",
            "task_id": record.task_id,
            "agent": record.agent_name,
            "a2a_status": record.status,
            "source": record.source,
        }
        content = build_completion_message(record)

        async with self._graph_lock:
            state = self._get_state()
            messages = list(state.get("messages", []))
            for existing in messages:
                existing_metadata = (
                    existing.get("metadata")
                    if isinstance(existing, dict)
                    else getattr(existing, "metadata", None)
                )
                if not isinstance(existing_metadata, dict):
                    continue
                if (
                    existing_metadata.get("type") == metadata["type"]
                    and existing_metadata.get("task_id") == record.task_id
                    and existing_metadata.get("a2a_status") == record.status
                ):
                    return

            message = ConversationMessage(
                id=str(uuid.uuid4()),
                role="tool",
                content=content,
                timestamp=utcnow(),
                metadata=metadata,
            )
            await self._state_manager.log_conversation(message)
            messages.append(message)
            await self._state_manager.save_messages(messages)
            state["messages"] = messages
