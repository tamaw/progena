"""Acknowledgements and request source names shared by UI features."""

from __future__ import annotations

from pydantic import BaseModel, Field

SOURCE_UI = "ui"
SOURCE_WORKFLOW = "workflow"


class UiRequestAcknowledgement(BaseModel):
    """Acknowledgement body for a dispatched finite UI request."""

    request_id: str
    event_types: list[str] = Field(min_length=1)


def acknowledgement_response(request_id: str, event_types: list[str]) -> UiRequestAcknowledgement:
    """Build an acknowledgement after dispatching causal UI event envelopes."""
    return UiRequestAcknowledgement(request_id=request_id, event_types=event_types)
