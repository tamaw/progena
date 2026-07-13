"""OpenAI-compatible Responses API and backend client models."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


@dataclass(slots=True)
class AgentResponse:
    """Normalized non-stream response payload returned from Progenitor."""

    response: str
    reasoning: str | None
    reasoning_tokens: int | None


class ResponseInputMessage(BaseModel):
    """Message format for the Responses API."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str | None = None


class ResponseRequest(BaseModel):
    """Unified Responses API request."""

    model: str = "progenitor-v1"
    input: list[ResponseInputMessage] | str
    stream: bool = False
    include_reasoning: bool | None = None
    metadata: dict[str, Any] | None = None


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
