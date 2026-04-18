"""State for the validate subagent graph."""

from __future__ import annotations

from typing import Any, TypedDict

from ...client import PlannedBuild


class ValidateState(TypedDict, total=False):
    request_text: str
    request_context: dict[str, Any]
    planned_build: PlannedBuild | None
    evidence_text: str
    validation_report: dict[str, Any]
    result_text: str
