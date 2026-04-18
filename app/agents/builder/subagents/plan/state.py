"""State for the plan subagent graph."""

from __future__ import annotations

from typing import Any, TypedDict

from ...client import PlannedBuild


class PlanState(TypedDict, total=False):
    request_text: str
    request_context: dict[str, Any]
    planned_build: PlannedBuild | None
    error: str | None
