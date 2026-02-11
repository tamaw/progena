"""
OpenAI-compatible API models.

Based on OpenAI Chat Completions API format.
https://platform.openai.com/docs/api-reference/chat
"""

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A message in the chat conversation."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: str = "progenitor-v1"
    messages: list[ChatMessage]
    temperature: float | None = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    stream: bool | None = False
    user: str | None = None

    # Additional fields we ignore but accept for compatibility
    top_p: float | None = None
    n: int | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, float] | None = None


class ChatCompletionChoice(BaseModel):
    """A choice in the chat completion response."""

    index: int = 0
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] = "stop"


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage = Field(default_factory=Usage)


class ChatCompletionChunk(BaseModel):
    """Streaming chunk for chat completion."""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[dict[str, Any]]


# Responses API (preferred)


class ResponseInputMessage(BaseModel):
    """Message format for the Responses API."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str


class ResponseOutputText(BaseModel):
    """Text part of a Responses output message."""

    type: Literal["output_text"] = "output_text"
    text: str


class ResponseOutputMessage(BaseModel):
    """Assistant message emitted by the Responses API."""

    role: Literal["assistant"] = "assistant"
    content: list[ResponseOutputText]


class ResponseRequest(BaseModel):
    """Unified Responses API request."""

    model: str = "progenitor-v1"
    input: list[ResponseInputMessage] | str
    stream: bool = False


class ResponsePayload(BaseModel):
    """Unified Responses API response payload."""

    id: str
    object: Literal["response"] = "response"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    output: list[ResponseOutputMessage]
    usage: Usage = Field(default_factory=Usage)


class ModelInfo(BaseModel):
    """Model information for /v1/models endpoint."""

    id: str
    object: Literal["model"] = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "progenitor"


class ModelsResponse(BaseModel):
    """Response for /v1/models endpoint."""

    object: Literal["list"] = "list"
    data: list[ModelInfo]


class ErrorDetail(BaseModel):
    """Error detail."""

    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class ErrorResponse(BaseModel):
    """Error response."""

    error: ErrorDetail


# CR-002: Simple chat request validation model
class SimpleChatRequest(BaseModel):
    """Simple chat request for /chat endpoint (CR-002)."""

    message: str = Field(..., min_length=1, max_length=100000)
    stream: bool = False


# CR-002: Enhanced validation for streaming requests
class StreamingChatRequest(ChatCompletionRequest):
    """Chat request with additional streaming validation (CR-002)."""

    @classmethod
    def validate_for_streaming(cls, request: "ChatCompletionRequest") -> None:
        """Additional validation for streaming requests."""
        if not request.messages:
            raise ValueError("Messages cannot be empty for streaming")

        # Validate message content isn't too large for streaming
        total_content_length = sum(
            len(msg.content) for msg in request.messages
        )
        if total_content_length > 500000:  # 500KB limit
            raise ValueError(
                f"Total message content ({total_content_length} chars) exceeds "
                "streaming limit of 500000 chars"
            )

        # Ensure at least one user message exists
        user_messages = [m for m in request.messages if m.role == "user"]
        if not user_messages:
            raise ValueError("At least one user message is required")


class StreamingResponseRequest(ResponseRequest):
    """Response request with additional streaming validation (CR-002)."""

    @classmethod
    def validate_for_streaming(cls, request: "ResponseRequest") -> None:
        """Additional validation for streaming requests."""
        # Check input size
        if isinstance(request.input, str):
            if len(request.input) > 500000:
                raise ValueError(
                    f"Input ({len(request.input)} chars) exceeds "
                    "streaming limit of 500000 chars"
                )
            if not request.input.strip():
                raise ValueError("Input cannot be empty")
        else:
            total_length = sum(
                len(m.content) if hasattr(m, 'content') else len(str(m))
                for m in request.input
            )
            if total_length > 500000:
                raise ValueError(
                    f"Total input ({total_length} chars) exceeds "
                    "streaming limit of 500000 chars"
                )
            if not request.input:
                raise ValueError("Input cannot be empty")
