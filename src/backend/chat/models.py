"""OpenAI-compatible Responses API and backend client models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


@dataclass(slots=True)
class AgentResponse:
    """Normalized non-stream response payload returned from Progena."""

    response: str
    reasoning: str | None = None
    reasoning_tokens: int | None = None
    usage: dict[str, int] | None = None
    deferred: bool = False
    deferred_started: bool = False
    terminal_status: str = "completed"
    terminal_error: str | None = None


def normalize_token_usage(value: Any) -> dict[str, int] | None:
    """Return a complete provider usage record only when every count is valid."""
    if not isinstance(value, dict):
        return None
    keys = ("input_tokens", "output_tokens", "total_tokens")
    if any(type(value.get(key)) is not int or value[key] < 0 for key in keys):
        return None
    return {key: value[key] for key in keys}


class ResponseInputMessage(BaseModel):
    """Message format for the Responses API."""

    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str | None = None


class ResponseRequest(BaseModel):
    """Unified Responses API request."""

    model: str = "progena-v1"
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
        """Additional validation for streaming requests.

        Empty text is allowed when the request carries attachments (an
        attachment-only send, e.g. drag-and-drop with no typed message) --
        see `_extract_response_input`'s matching allowance and
        `_resolve_message_attachments`, which gives the agent a note about
        the attachment even with no user text.
        """
        has_attachments = bool(
            isinstance(request.metadata, dict) and request.metadata.get("attachments")
        )
        if isinstance(request.input, str):
            if len(request.input) > 500000:
                raise ValueError(
                    f"Input ({len(request.input)} chars) exceeds streaming limit of 500000 chars"
                )
            if not request.input.strip() and not has_attachments:
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
            if not request.input and not has_attachments:
                raise ValueError("Input cannot be empty")
