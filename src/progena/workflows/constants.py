"""Workflow limits, fixed schemas and type aliases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

WORKFLOW_DEFAULT_DOCUMENT_TYPE = "kanban_card"


WORKFLOW_VERSION = 3


# Max chained Progena-initiated workflow invocations.
MAX_WORKFLOW_INVOKE_DEPTH = 3


# Upper bound on documents one document_query node may return.
MAX_WORKFLOW_QUERY_LIMIT = 200


WorkflowRunStatus = Literal["queued", "running", "paused", "completed", "failed"]


WorkflowRunSource = Literal["document", "manual", "progena", "cron"]


WorkflowCronOverlap = Literal["skip", "queue"]


_JSON_OBJECT_SCHEMA = {"type": "object"}


_ANY_VALUE_SCHEMA: dict[str, Any] = {}


_TRIGGER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["trigger"],
    "properties": {"trigger": _ANY_VALUE_SCHEMA},
}


DocumentQueryOperator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "exists"]


CRON_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scheduled_for", "fired_at", "timezone"],
    "properties": {
        "scheduled_for": {"type": "string", "format": "date-time"},
        "fired_at": {"type": "string", "format": "date-time"},
        "timezone": {"type": "string"},
    },
}


# Looks up one routine's graph document by id; None when no such routine exists.
RoutineLoader = Callable[[str], Mapping[str, Any] | None]


ROUTINE_MESSAGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["message"],
    "properties": {"message": {"type": "string"}},
}
