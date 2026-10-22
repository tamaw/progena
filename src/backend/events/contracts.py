"""Canonical browser event envelope shared by all features."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class UiEventEnvelope(BaseModel):
    id: str
    seq: int
    type: str
    scope: Literal["global", "session", "chat"] = "global"
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=_now_iso)
