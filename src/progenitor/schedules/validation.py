"""Validation capabilities."""

from __future__ import annotations

import json

from croniter import croniter

from src.progenitor.documents.catalog import check_entry_schema
from src.progenitor.documents.values import JsonDoc
from src.progenitor.schedules.engine import parse_schedule_datetime
from src.progenitor.utils import utcnow


def validate_schedule_document(content: str) -> JsonDoc:
    """Parse + validate the whole ``schedule.json`` payload.

    ``schedule.json`` is a JSON object keyed by schedule id (mirrors
    ``mcp.json``'s ``mcpServers`` map): an entry's key is authoritative for its
    id, so entry bodies do not repeat it. Also checks that each entry's
    ``cron_expression``/``scheduled_at`` is actually parseable, not just a
    string - this is the only validation a write gets, since there's no
    dedicated create/update/delete tool; the ``managing-schedules`` skill
    reads, edits, and writes this one file directly, the same way
    ``managing-mcp`` does for ``mcp.json``.

    Every field is type-checked with ``isinstance`` (not just coerced) so a
    malformed entry (e.g. a non-string ``message``) is rejected here with a
    clean ``ValueError`` rather than reaching ``ScheduleEntry(**fields)`` at
    save time and raising an uncaught ``pydantic.ValidationError``. Exactly
    one of ``cron_expression``/``scheduled_at`` must be set, mirroring the
    check the old ``add_scheduled_task`` tool used to make before this
    document became agent-editable.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object keyed by schedule id")

    for entry_id, item in data.items():
        if not isinstance(item, dict):
            raise ValueError(f"entry '{entry_id}' must be an object")
        check_entry_schema(f"entry '{entry_id}'", item, "schedule_entry")

        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"entry '{entry_id}' is missing 'message'")

        cron_expression = item.get("cron_expression")
        if cron_expression is not None and not isinstance(cron_expression, str):
            raise ValueError(f"entry '{entry_id}' has a non-string cron_expression")
        scheduled_at = item.get("scheduled_at")
        if scheduled_at is not None and not isinstance(scheduled_at, str):
            raise ValueError(f"entry '{entry_id}' has a non-string scheduled_at")
        timezone = item.get("timezone")
        if timezone is not None and not isinstance(timezone, str):
            raise ValueError(f"entry '{entry_id}' has a non-string timezone")
        user_request = item.get("user_request")
        if user_request is not None and not isinstance(user_request, str):
            raise ValueError(f"entry '{entry_id}' has a non-string user_request")
        paused = item.get("paused", False)
        if not isinstance(paused, bool):
            raise ValueError(f"entry '{entry_id}' has a non-bool paused")

        if bool(cron_expression) == bool(scheduled_at):
            raise ValueError(
                f"entry '{entry_id}' must set exactly one of "
                "cron_expression/scheduled_at, not both or neither"
            )

        if cron_expression:
            try:
                croniter(str(cron_expression), utcnow())
            except Exception as exc:
                raise ValueError(
                    f"entry '{entry_id}' has invalid cron_expression: {cron_expression}"
                ) from exc

        if scheduled_at and parse_schedule_datetime(scheduled_at) is None:
            raise ValueError(
                f"entry '{entry_id}' has invalid scheduled_at timestamp: {scheduled_at}"
            )

    return data
