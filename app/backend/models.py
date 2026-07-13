"""OpenAI-compatible Responses API and backend client models."""

import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


@dataclass(slots=True)
class AgentResponse:
    """Normalized non-stream response payload returned from Progenitor."""

    response: str
    reasoning: str | None
    reasoning_tokens: int | None


class Usage(BaseModel):
    """Token usage information."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ResponseInputMessage(BaseModel):
    """Message format for the Responses API."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str | None = None


class ResponseOutputText(BaseModel):
    """Text part of a Responses output message."""

    type: Literal["output_text"] = "output_text"
    text: str


class ResponseReasoningSummaryText(BaseModel):
    """Reasoning summary text item emitted by the Responses API."""

    type: Literal["summary_text"] = "summary_text"
    text: str


class ResponseOutputReasoning(BaseModel):
    """Reasoning part of a Responses output message."""

    type: Literal["reasoning"] = "reasoning"
    summary: list[ResponseReasoningSummaryText]


class ResponseOutputMessage(BaseModel):
    """Assistant message emitted by the Responses API."""

    role: Literal["assistant"] = "assistant"
    content: list[ResponseOutputText | ResponseOutputReasoning]


class ResponseRequest(BaseModel):
    """Unified Responses API request."""

    model: str = "progenitor-v1"
    input: list[ResponseInputMessage] | str
    stream: bool = False
    include_reasoning: bool | None = None
    metadata: dict[str, Any] | None = None


class ResponsePayload(BaseModel):
    """Unified Responses API response payload."""

    id: str
    object: Literal["response"] = "response"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    output: list[ResponseOutputMessage]
    usage: Usage = Field(default_factory=Usage)


class ErrorDetail(BaseModel):
    """Error detail."""

    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class ErrorResponse(BaseModel):
    """Standard API error response wrapper."""

    error: ErrorDetail


class StreamingResponseRequest(ResponseRequest):
    """Response request with additional streaming validation."""

    @classmethod
    def validate_for_streaming(cls, request: ResponseRequest) -> None:
        """Additional validation for streaming requests."""
        if isinstance(request.input, str):
            if len(request.input) > 500000:
                raise ValueError(
                    f"Input ({len(request.input)} chars) exceeds streaming limit of 500000 chars"
                )
            if not request.input.strip():
                raise ValueError("Input cannot be empty")
        else:
            total_length = 0
            for message in request.input:
                content = getattr(message, "content", None)
                total_length += len(str(message)) if content is None else len(content)
            if total_length > 500000:
                raise ValueError(
                    f"Total input ({total_length} chars) exceeds streaming limit of 500000 chars"
                )
            if not request.input:
                raise ValueError("Input cannot be empty")
