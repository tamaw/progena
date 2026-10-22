"""
Conversation Metadata - Rich per-message metadata tracking.

Based on patterns from text-generation-webui for reasoning trace tracking
and conversation history with metadata.

Features:
- Per-message metadata with {role}_{index} keying
- Timestamp tracking
- Attachment support (files, URLs, search results)
- Message versioning and branching
- Tool call metadata tracking
- Reasoning traces per message
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AttachmentType(StrEnum):
    """Type of attachment."""

    TEXT = "text/plain"
    HTML = "text/html"
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    JSON = "application/json"
    IMAGE = "image/*"
    URL = "url"
    SEARCH_RESULT = "search_result"


class Attachment(BaseModel):
    """An attachment to a message (file, URL, search result)."""

    id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:8]}")
    name: str
    type: AttachmentType = AttachmentType.TEXT
    url: str | None = None
    content: str | None = None  # Extracted/truncated content
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ToolCallRecord(BaseModel):
    """Record of a tool call made during message generation."""

    id: str = Field(default_factory=lambda: f"tc_{uuid.uuid4().hex[:8]}")
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str | None = None
    error: str | None = None
    duration_ms: float | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ReasoningStep(BaseModel):
    """A step in the reasoning trace."""

    step_number: int
    thought: str
    action: str | None = None
    observation: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class MessageMetadata(BaseModel):
    """Rich metadata for a conversation message."""

    # Identity
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    parent_id: str | None = None  # For branching conversations
    version: int = 1  # For message versioning

    # Timing
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    generation_start: datetime | None = None
    generation_end: datetime | None = None
    generation_duration_ms: float | None = None

    # Attachments
    attachments: list[Attachment] = Field(default_factory=list)

    # Tool calls
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)

    # Reasoning trace
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    thinking_content: str | None = None  # Raw thinking/reasoning content

    # Model info
    model_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    # Request correlation
    request_id: str | None = None
    session_id: str | None = None

    # Custom fields
    custom: dict[str, Any] = Field(default_factory=dict)

    def add_attachment(
        self,
        name: str,
        content: str,
        attachment_type: AttachmentType = AttachmentType.TEXT,
        url: str | None = None,
        **metadata,
    ) -> Attachment:
        """Add an attachment to this message."""
        attachment = Attachment(
            name=name,
            type=attachment_type,
            content=content,
            url=url,
            size_bytes=len(content.encode("utf-8")) if content else None,
            metadata=metadata,
        )
        self.attachments.append(attachment)
        return attachment

    def add_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str | None = None,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> ToolCallRecord:
        """Record a tool call."""
        record = ToolCallRecord(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            error=error,
            duration_ms=duration_ms,
        )
        self.tool_calls.append(record)
        return record

    def add_reasoning_step(
        self,
        thought: str,
        action: str | None = None,
        observation: str | None = None,
    ) -> ReasoningStep:
        """Add a reasoning step."""
        step = ReasoningStep(
            step_number=len(self.reasoning_steps) + 1,
            thought=thought,
            action=action,
            observation=observation,
        )
        self.reasoning_steps.append(step)
        return step

    def set_generation_timing(
        self,
        start: datetime,
        end: datetime,
    ) -> None:
        """Set generation timing information."""
        self.generation_start = start
        self.generation_end = end
        self.generation_duration_ms = (end - start).total_seconds() * 1000

    def set_token_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Set token usage information."""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class ConversationMessage(BaseModel):
    """A message in the conversation with full metadata."""

    role: str  # user | assistant | system | tool
    content: str
    metadata: MessageMetadata = Field(default_factory=MessageMetadata)

    # Internal tracking (separate visible vs internal content like webui)
    visible_content: str | None = None  # What user sees (may have redactions)

    @property
    def id(self) -> str:
        """Get message ID."""
        return self.metadata.message_id

    @property
    def timestamp(self) -> datetime:
        """Get message timestamp."""
        return self.metadata.timestamp

    def create_version(self, new_content: str) -> ConversationMessage:
        """Create a new version of this message with updated content."""
        new_metadata = self.metadata.model_copy(deep=True)
        new_metadata.version += 1
        new_metadata.parent_id = self.metadata.message_id
        new_metadata.message_id = f"msg_{uuid.uuid4().hex[:8]}"
        new_metadata.timestamp = datetime.utcnow()

        return ConversationMessage(
            role=self.role,
            content=new_content,
            metadata=new_metadata,
        )


@dataclass
class ConversationHistory:
    """
    Rich conversation history with per-message metadata tracking.

    Supports:
    - Internal vs visible history tracks
    - Metadata keyed by {role}_{index}
    - Message versioning and branching
    - Conversation replay/debugging
    """

    # Internal history (full content for LLM)
    internal: list[ConversationMessage] = field(default_factory=list)

    # Visible history (what user sees, may differ)
    visible: list[ConversationMessage] = field(default_factory=list)

    # Metadata indexed by message key
    _metadata: dict[str, MessageMetadata] = field(default_factory=dict)

    # Message index by ID
    _index: dict[str, int] = field(default_factory=dict)

    # Branching support
    _branches: dict[str, list[str]] = field(default_factory=dict)  # parent_id -> [child_ids]

    def _make_key(self, role: str, index: int) -> str:
        """Create metadata key from role and index."""
        return f"{role}_{index}"

    def add_message(
        self,
        role: str,
        content: str,
        visible_content: str | None = None,
        metadata: MessageMetadata | None = None,
    ) -> ConversationMessage:
        """Add a message to the history."""
        msg_metadata = metadata or MessageMetadata()
        message = ConversationMessage(
            role=role,
            content=content,
            visible_content=visible_content or content,
            metadata=msg_metadata,
        )

        index = len(self.internal)
        self.internal.append(message)
        self.visible.append(message)

        # Index by key and ID
        key = self._make_key(role, index)
        self._metadata[key] = msg_metadata
        self._index[msg_metadata.message_id] = index

        return message

    def get_message(self, message_id: str) -> ConversationMessage | None:
        """Get message by ID."""
        index = self._index.get(message_id)
        if index is not None and 0 <= index < len(self.internal):
            return self.internal[index]
        return None

    def get_metadata(self, role: str, index: int) -> MessageMetadata | None:
        """Get metadata by role and index."""
        key = self._make_key(role, index)
        return self._metadata.get(key)

    def update_metadata(self, role: str, index: int, **fields) -> None:
        """Update metadata fields for a message."""
        key = self._make_key(role, index)
        if key not in self._metadata:
            self._metadata[key] = MessageMetadata()

        metadata = self._metadata[key]
        for field_name, field_value in fields.items():
            if hasattr(metadata, field_name):
                setattr(metadata, field_name, field_value)
            else:
                metadata.custom[field_name] = field_value

    def get_message_by_index(self, index: int) -> ConversationMessage | None:
        """Get message by index."""
        if 0 <= index < len(self.internal):
            return self.internal[index]
        return None

    def edit_message(
        self,
        message_id: str,
        new_content: str,
        create_branch: bool = True,
    ) -> ConversationMessage | None:
        """Edit a message, optionally creating a branch."""
        original = self.get_message(message_id)
        if not original:
            return None

        if create_branch:
            # Create new version
            new_msg = original.create_version(new_content)
            index = len(self.internal)
            self.internal.append(new_msg)

            # Track branch
            if message_id not in self._branches:
                self._branches[message_id] = []
            self._branches[message_id].append(new_msg.id)

            self._index[new_msg.id] = index
            return new_msg
        else:
            # In-place edit
            original.content = new_content
            original.metadata.version += 1
            original.metadata.timestamp = datetime.utcnow()
            return original

    def get_branches(self, message_id: str) -> list[str]:
        """Get IDs of branched versions of a message."""
        return self._branches.get(message_id, [])

    def iter_messages(
        self,
        roles: list[str] | None = None,
    ) -> Iterator[tuple[int, ConversationMessage]]:
        """Iterate messages, optionally filtering by role."""
        for i, msg in enumerate(self.internal):
            if roles is None or msg.role in roles:
                yield i, msg

    def get_user_messages(self) -> list[ConversationMessage]:
        """Get all user messages."""
        return [msg for msg in self.internal if msg.role == "user"]

    def get_assistant_messages(self) -> list[ConversationMessage]:
        """Get all assistant messages."""
        return [msg for msg in self.internal if msg.role == "assistant"]

    def get_last_user_message(self) -> ConversationMessage | None:
        """Get the most recent user message."""
        for msg in reversed(self.internal):
            if msg.role == "user":
                return msg
        return None

    def get_last_assistant_message(self) -> ConversationMessage | None:
        """Get the most recent assistant message."""
        for msg in reversed(self.internal):
            if msg.role == "assistant":
                return msg
        return None

    def to_openai_format(self) -> list[dict[str, str]]:
        """Convert to OpenAI messages format."""
        return [{"role": msg.role, "content": msg.content} for msg in self.internal]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for persistence."""
        return {
            "internal": [msg.model_dump() for msg in self.internal],
            "visible": [msg.model_dump() for msg in self.visible],
            "metadata": {k: v.model_dump() for k, v in self._metadata.items()},
            "branches": self._branches,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationHistory:
        """Deserialize from dictionary."""
        history = cls()

        for msg_data in data.get("internal", []):
            msg = ConversationMessage.model_validate(msg_data)
            history.internal.append(msg)
            history._index[msg.id] = len(history.internal) - 1

        for msg_data in data.get("visible", []):
            msg = ConversationMessage.model_validate(msg_data)
            history.visible.append(msg)

        for key, meta_data in data.get("metadata", {}).items():
            history._metadata[key] = MessageMetadata.model_validate(meta_data)

        history._branches = data.get("branches", {})

        return history

    def clear(self) -> None:
        """Clear all messages and metadata."""
        self.internal.clear()
        self.visible.clear()
        self._metadata.clear()
        self._index.clear()
        self._branches.clear()

    def __len__(self) -> int:
        return len(self.internal)

    def __iter__(self):
        return iter(self.internal)

    def __getitem__(self, index: int) -> ConversationMessage:
        return self.internal[index]


def truncate_content_by_tokens(
    content: str,
    max_tokens: int = 8192,
    tokenizer=None,
) -> str:
    """
    Truncate content to fit within token limit using binary search.

    Falls back to character-based estimation if no tokenizer provided.
    """
    # Simple character estimation if no tokenizer
    if tokenizer is None:
        # Rough estimate: 4 chars per token
        max_chars = max_tokens * 4
        if len(content) <= max_chars:
            return content
        return content[:max_chars] + "... [truncated]"

    # Token-based truncation with binary search
    token_count = len(tokenizer.encode(content))
    if token_count <= max_tokens:
        return content

    left, right = 0, len(content)
    while left < right:
        mid = (left + right + 1) // 2
        if len(tokenizer.encode(content[:mid])) <= max_tokens:
            left = mid
        else:
            right = mid - 1

    return content[:left] + "... [truncated]"
