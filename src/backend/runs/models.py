"""Typed browser read model for execution runs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RunKind = Literal["routine", "workflow", "schedule"]
SpanKind = Literal["run", "queue", "step", "routine", "agent", "model", "tool"]


class RunRow(BaseModel):
    kind: RunKind
    run_id: str
    name: str
    trigger: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    steps_done: int | None = None
    steps_total: int | None = None


class Span(BaseModel):
    id: str
    parent_id: str | None = None
    kind: SpanKind
    name: str
    operation: str | None = None
    start: str
    end: str | None = None
    status: str
    tokens: int | None = None
    summary: str | None = None
    error: str | None = None
    timing_source: str | None = None


class RunTrace(BaseModel):
    kind: RunKind
    run_id: str
    name: str
    status: str
    started_at: str
    completed_at: str | None = None
    spans: list[Span] = Field(default_factory=list)
    tokens: int | None = None


class RunsSubscription(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    kind: RunKind | None = None
    run_id: str | None = None

    def selected(self) -> bool:
        return self.kind is not None and self.run_id is not None
