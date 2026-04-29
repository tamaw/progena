"""Shared response models for Progenitor clients."""

from dataclasses import dataclass


@dataclass(slots=True)
class AgentResponse:
    """Normalized non-stream response payload returned from Progenitor."""

    response: str
    reasoning: str | None
    reasoning_tokens: int | None
