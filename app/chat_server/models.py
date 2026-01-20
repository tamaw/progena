"""
OpenAI-compatible API models.

Based on OpenAI Chat Completions API format.
https://platform.openai.com/docs/api-reference/chat
"""

from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
import time


class ChatMessage(BaseModel):
    """A message in the chat conversation."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""

    model: str = "progenitor-v1"
    messages: list[ChatMessage]
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    stream: Optional[bool] = False
    user: Optional[str] = None

    # Additional fields we ignore but accept for compatibility
    top_p: Optional[float] = None
    n: Optional[int] = None
    stop: Optional[str | list[str]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[dict[str, float]] = None


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

    # Accept legacy chat format as a fallback for compatibility
    messages: Optional[list[ChatMessage]] = None


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
    param: Optional[str] = None
    code: Optional[str] = None


class ErrorResponse(BaseModel):
    """Error response."""

    error: ErrorDetail
