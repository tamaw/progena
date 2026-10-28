"""Payload construction for workflow document and clock triggers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.progena.cron import resolve_timezone, timezone_name
from src.progena.documents.types import DocumentOperation, DocumentType
from src.progena.workflows.models import WorkflowNodeCron
from src.progena.workflows.values import json_equal


def document_changed_fields(before: Any, after: Any) -> list[str]:
    """Top-level fields whose value differs between two document payloads."""

    previous = before if isinstance(before, Mapping) else {}
    current = after if isinstance(after, Mapping) else {}
    return sorted(
        field
        for field in set(previous) | set(current)
        if field not in previous
        or field not in current
        or not json_equal(previous[field], current[field])
    )


def build_document_event_output(
    *,
    event_id: int,
    document_type: DocumentType,
    key: str,
    op: DocumentOperation,
    before: Any,
    after: Any,
    timestamp: str,
) -> dict[str, Any]:
    """Build the output object an event node emits for one document event."""

    output: dict[str, Any] = {
        "event_id": event_id,
        "document_type": document_type.id,
        "key": key,
        "id": document_type.document_id(key),
        "op": op,
        "timestamp": timestamp,
        "changed_fields": document_changed_fields(before, after),
    }
    if before is not None:
        output["before"] = before
    if after is not None:
        output["after"] = after
    return output


def build_document_read_output(
    document_type: DocumentType, key: str, document: Any | None
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "found": document is not None,
        "key": key,
        "id": document_type.document_id(key),
    }
    if document is not None:
        output["document"] = document
    return output


def build_cron_trigger_output(
    node: WorkflowNodeCron,
    *,
    scheduled_for: datetime,
    fired_at: datetime,
    preference_timezone: str = "UTC",
) -> dict[str, Any]:
    """The output object a cron node emits for one due time."""

    effective_timezone = timezone_name(node.timezone, preference_timezone)
    zone = resolve_timezone(effective_timezone)
    return {
        "scheduled_for": scheduled_for.astimezone(zone).isoformat(),
        "fired_at": fired_at.astimezone(zone).isoformat(),
        "timezone": effective_timezone,
    }
