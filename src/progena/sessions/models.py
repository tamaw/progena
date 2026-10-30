"""Sessions domain contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.progena.utils import utcnow

# Internal runtime vendor projections; browser delivery remains canonical SSE.
CONVERSATION_MESSAGES_INCLUDE = "progena.langchain_messages"
CONVERSATION_MESSAGES_FIELD = "progena_langchain_messages"
CONVERSATION_SUMMARIES_INCLUDE = "progena.conversation_summaries"
CONVERSATION_SUMMARIES_FIELD = "progena_summary_occurrences"


def generate_thread_id() -> str:
    """Generate a unique thread ID for conversation tracking."""
    return f"thread_{uuid.uuid4().hex[:12]}"


class ConversationMessage(BaseModel):
    """A message in the conversation history."""

    id: str
    role: str
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
