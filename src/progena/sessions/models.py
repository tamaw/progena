"""Sessions domain contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.progena.utils import utcnow


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
