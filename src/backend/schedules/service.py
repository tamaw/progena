"""Schedules service."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from croniter import croniter
from fastapi import HTTPException

import src.backend.events.contracts as events_contracts
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.monitoring.state as monitoring_state
import src.backend.runtime_liveness as runtime_liveness
import src.backend.schedules.models as schedules_models
import src.backend.settings.preferences_store as preferences_store
import src.backend.storage.documents as storage_documents
from src.progena.cron import timezone_name as valid_timezone_name
from src.progena.documents.types import DOCUMENT_OPERATIONS
from src.progena.runtime import ownership
from src.progena.schedules.engine import Scheduler, parse_schedule_datetime
from src.progena.utils import normalize_utc_datetime, utcnow

logger = logging.getLogger(__name__)


def _next_run_iso(next_run: datetime) -> str:
    """Serialize scheduler-produced instants as UTC for stored/UI schedule state."""
    normalized = normalize_utc_datetime(next_run)
    assert normalized is not None
    return normalized.isoformat().replace("+00:00", "Z")


def _schedule_item_to_info(
    item,
    runs: list[schedules_models.ScheduleRunInfo] | None = None,
    preference_timezone: str = "UTC",
) -> schedules_models.ScheduleItemInfo:
    """Build a schedule response with a freshly computed next_run.

    ``ScheduleItem.next_run`` falls back to ``last_run`` because the stored entry
    carries no ``next_run`` field. Recompute it here with the same scheduler the
    runtime uses so the UI shows the actual next fire time.
    """
    raw = item.raw if isinstance(item.raw, dict) else {}
    next_run_dt = Scheduler().next_run_for_entry(raw, preference_timezone)
    paused = bool(raw.get("paused", not raw.get("enabled", item.enabled)))
    cron_expression = raw.get("cron_expression") or None
    raw_scheduled_at = raw.get("scheduled_at")
    parsed_scheduled_at = parse_schedule_datetime(raw_scheduled_at)
    scheduled_at = parsed_scheduled_at.isoformat() if parsed_scheduled_at else None
    schedule_type = raw.get("schedule_type") or (
        "once" if scheduled_at else "cron" if cron_expression else None
    )
    last_run = raw.get("last_run")
    last_run_status = raw.get("last_run_status")
    if (
        last_run_status in {None, "", "idle"}
        and schedule_type == "once"
        and paused
        and last_run
        and next_run_dt is None
    ):
        last_run_status = "completed"
    display_schedule = (
        scheduled_at or item.schedule or ""
        if schedule_type == "once"
        else item.schedule or cron_expression or scheduled_at or ""
    )
    return schedules_models.ScheduleItemInfo(
        id=item.id,
        name=item.name,
        schedule=display_schedule,
        next_run=_next_run_iso(next_run_dt) if next_run_dt else None,
        enabled=not paused,
        paused=paused,
        last_run=last_run,
        created_at=raw.get("created_at"),
        message=raw.get("message") or item.name,
        cron_expression=cron_expression,
        scheduled_at=scheduled_at,
        schedule_type=schedule_type,
        timezone=raw.get("timezone"),
        last_run_status=last_run_status,
        runs=runs or [],
    )


def _widget_to_response(widget: dict) -> schedules_models.WidgetResponse:
    """Normalize state-reader widget payloads for API responses."""
    payload = dict(widget)
    return schedules_models.WidgetResponse(
        id=payload.get("id", ""),
        type=payload.get("type", "unknown"),
        title=payload.get("title") or payload.get("name", ""),
        name=payload.get("name", ""),
        status=payload.get("status", "unknown"),
        description=payload.get("description"),
        summary=payload.get("summary"),
        updated_at=payload.get("updated_at"),
        data=payload,
    )


def _normalize_event_widget_record(entry: dict) -> dict:
    event_type = str(entry.get("event_type") or "kanban_card.created").strip().lower()
    name = str(entry.get("name") or event_type or "Event").strip()
    last_payload = entry.get("last_payload")
    summary = str(entry.get("summary") or f"Waiting for {event_type}").strip()
    try:
        trigger_count = int(entry.get("trigger_count", 0) or 0)
    except (TypeError, ValueError):
        trigger_count = 0
    return {
        "id": str(entry.get("id") or ""),
        "name": name,
        "event_type": event_type,
        "enabled": bool(entry.get("enabled", True)),
        "trigger_count": trigger_count,
        "last_triggered_at": entry.get("last_triggered_at"),
        "last_run_status": str(entry.get("last_run_status") or "idle"),
        "last_payload": last_payload if isinstance(last_payload, dict) else None,
        "summary": summary,
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at") or entry.get("created_at"),
    }


def _read_event_widget_records(state_reader: monitoring_state.StateReader) -> dict[str, dict]:
    document = state_reader.document_store.get_document(
        storage_documents.BACKEND_STATE_PREFIX, "event_widgets", {}
    )
    raw = document.payload if document is not None else {}
    if not isinstance(raw, dict):
        return {}
    return {
        entry_id: _normalize_event_widget_record({**entry, "id": entry.get("id") or entry_id})
        for entry_id, entry in raw.items()
        if isinstance(entry, dict)
    }


def _write_event_widget_records(
    state_reader: monitoring_state.StateReader, records: dict[str, dict]
) -> dict[str, dict]:
    normalized = {
        entry_id: _normalize_event_widget_record({**entry, "id": entry.get("id") or entry_id})
        for entry_id, entry in records.items()
        if isinstance(entry, dict)
    }
    state_reader.document_store.save_document(
        storage_documents.BACKEND_STATE_PREFIX, "event_widgets", normalized
    )
    return normalized


def _validate_event_type(value: str) -> str:
    """Validate an event widget name: ``<document_type>.<op>``."""

    normalized = (value or "").strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="Event type is required")
    type_id, _, op = normalized.rpartition(".")
    if op not in DOCUMENT_OPERATIONS or type_id not in events_documents.list_document_types(
        storage_documents.get_default_document_store()
    ):
        raise HTTPException(status_code=400, detail=f"Unsupported event type: {value}")
    return normalized


def _build_event_widget_entry(
    *,
    event_widget_id: str,
    name: str,
    event_type: str,
    enabled: bool,
    created_at: str,
) -> dict:
    normalized_type = _validate_event_type(event_type)
    return {
        "id": event_widget_id,
        "name": name,
        "event_type": normalized_type,
        "enabled": enabled,
        "trigger_count": 0,
        "last_triggered_at": None,
        "last_run_status": "idle",
        "last_payload": None,
        "summary": f"Waiting for {normalized_type}",
        "created_at": created_at,
        "updated_at": created_at,
    }


def _read_schedule_config(state_reader: monitoring_state.StateReader) -> dict[str, Any]:
    """Read the live schedule config, keyed by schedule id."""
    document = state_reader.document_store.get_document(
        storage_documents.PROGENA_STATE_PREFIX, "schedule", {}
    )
    raw = document.payload if document is not None else {}
    return raw if isinstance(raw, dict) else {}


def _delete_schedule_entry(state_reader: monitoring_state.StateReader, schedule_id: str) -> bool:
    """Delete a persisted schedule entry and refresh the cached schedule document."""
    schedule_data = _read_schedule_config(state_reader)

    def _save() -> None:
        state_reader.document_store.save_document(
            storage_documents.PROGENA_STATE_PREFIX, "schedule", schedule_data
        )

    return _delete_document_entry(entries=schedule_data, entry_id=schedule_id, save=_save)


def _broadcast_widget_refresh(
    state_reader: monitoring_state.StateReader,
    *,
    request_id: str | None = None,
    refreshed_widget: dict[str, Any] | None = None,
) -> None:
    """Notify status listeners that backend-derived widgets changed.

    A live agent inspection is ephemeral: it comes from the agent card rather
    than a state document.  Merge it into this event's snapshot so the UI can
    consume the inspection without fabricating a local response body.
    """
    items = state_reader.get_widgets()
    if refreshed_widget is not None:
        items = [
            refreshed_widget if item.get("id") == refreshed_widget.get("id") else item
            for item in items
        ]
    events_stream.broadcast_ui_event("widgets.updated", {"items": items}, request_id=request_id)


def _find_widget_payload(
    state_reader: monitoring_state.StateReader, widget_id: str
) -> dict[str, Any]:
    widget = next(
        (item for item in state_reader.get_widgets() if str(item.get("id")) == widget_id),
        None,
    )
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    return dict(widget)


def _format_httpx_error(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip()
        if len(body) > 240:
            body = f"{body[:237]}..."
        return (
            f"{exc.request.method} {exc.request.url} returned "
            f"{exc.response.status_code}: {body or exc.response.reason_phrase}"
        )
    if isinstance(exc, httpx.RequestError):
        return f"Request to {exc.request.url} failed: {exc}"
    return str(exc)


def _agent_card_url(agent: Any) -> str | None:
    direct = str(getattr(agent, "agent_card_url", "") or "").strip()
    if direct:
        return direct

    base_url = str(getattr(agent, "url", "") or "").strip().rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/.well-known/agent-card.json"


async def _inspect_agent_widget(
    widget: dict[str, Any], state_reader: monitoring_state.StateReader
) -> dict[str, Any]:
    agent_name = str(widget.get("name") or "").strip()
    agents, _ = state_reader.get_registry()
    agent = next((entry for entry in agents if entry.name == agent_name and entry.enabled), None)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent widget is not registered")

    card_url = _agent_card_url(agent)
    if card_url is None:
        raise HTTPException(status_code=400, detail="Agent is missing an agent-card URL")

    headers = {"Accept": "application/json"}
    if agent.headers:
        headers.update(agent.headers)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), headers=headers) as client:
            response = await client.get(card_url)
            response.raise_for_status()
            payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent card returned invalid JSON: {exc}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_format_httpx_error(exc)) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Agent card response must be a JSON object")

    default_input_modes = payload.get("defaultInputModes")
    default_output_modes = payload.get("defaultOutputModes")
    skills = payload.get("skills")
    inspection = {
        "source": "a2a",
        "refreshed_at": utcnow().isoformat(),
        "card_url": card_url,
        "protocol_version": payload.get("protocolVersion"),
        "preferred_transport": payload.get("preferredTransport"),
        "default_input_modes": default_input_modes if isinstance(default_input_modes, list) else [],
        "default_output_modes": default_output_modes
        if isinstance(default_output_modes, list)
        else [],
        "capabilities": payload.get("capabilities"),
        "skills": skills if isinstance(skills, list) else [],
        "card": payload,
    }

    return {
        **widget,
        "description": str(payload.get("description") or widget.get("description") or ""),
        "summary": str(widget.get("summary") or payload.get("description") or ""),
        "url": str(payload.get("url") or getattr(agent, "url", "") or ""),
        "agent_card_url": card_url,
        "version": str(payload.get("version") or getattr(agent, "version", "") or ""),
        "availability_status": "online",
        "inspection": inspection,
    }


def document_event_name(document_type_id: str, op: str) -> str:
    """Event widget name for one document event, e.g. ``kanban_card.created``."""

    return f"{document_type_id}.{op}"


def _summarize_document_event(typed: events_documents.TypedDocumentEvent) -> str:
    label = typed.document_type.label if typed.document_type else typed.event.key
    document = typed.event.after if typed.event.after is not None else typed.event.before
    title = str(document.get("title") or "").strip() if isinstance(document, dict) else ""
    suffix = f" for {title}" if title else ""
    return f"{label} {typed.event.op}{suffix}"


async def _apply_document_event_widgets(typed: events_documents.TypedDocumentEvent) -> None:
    """Count document events on every enabled event widget subscribed to them."""

    if typed.document_type is None or typed.output is None:
        return
    state_reader = monitoring_state.get_state_reader()
    event_widgets = _read_event_widget_records(state_reader)
    if not event_widgets:
        return
    name = document_event_name(typed.document_type.id, typed.event.op)
    timestamp = typed.event.created_at.isoformat()
    summary = _summarize_document_event(typed)
    payload = {
        field: typed.output[field]
        for field in ("event_id", "document_type", "key", "id", "op", "changed_fields")
    }
    changed = False
    for widget in event_widgets.values():
        if not widget.get("enabled", True) or widget.get("event_type") != name:
            continue
        widget["trigger_count"] = int(widget.get("trigger_count", 0) or 0) + 1
        widget["last_triggered_at"] = timestamp
        widget["last_run_status"] = "success"
        widget["last_payload"] = payload
        widget["summary"] = summary
        widget["updated_at"] = timestamp
        changed = True
    if changed:
        _write_event_widget_records(state_reader, event_widgets)
        _broadcast_widget_refresh(state_reader)


def _parse_once_datetime(expression: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(expression.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="One-off schedules must use an ISO datetime like 2026-03-15T14:30:00Z",
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _interval_to_cron(expression: str) -> str:
    match = re.fullmatch(
        r"\s*(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*",
        expression,
        flags=re.IGNORECASE,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Interval schedules must look like 5m, 2h, or 1d",
        )

    value = int(match.group(1))
    unit = match.group(2).lower()
    if value <= 0:
        raise HTTPException(status_code=400, detail="Interval schedules must be greater than zero")

    if unit in {"s", "sec", "secs", "second", "seconds"}:
        if value % 60 != 0:
            raise HTTPException(
                status_code=400,
                detail="Second-based intervals must be divisible by 60",
            )
        value //= 60
        unit = "minute"

    if unit in {"m", "min", "mins", "minute", "minutes"}:
        return f"*/{value} * * * *"
    if unit in {"h", "hr", "hrs", "hour", "hours"}:
        return f"0 */{value} * * *"
    return f"0 0 */{value} * *"


def _resolve_schedule_expression(
    schedule_type: str,
    expression: str,
) -> tuple[str, str | None, str | None]:
    normalized_type = (schedule_type or "cron").strip().lower()
    normalized_expression = expression.strip()
    if not normalized_expression:
        raise HTTPException(status_code=400, detail="Schedule expression is required")

    if normalized_type == "cron":
        try:
            croniter(normalized_expression, utcnow())
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid cron expression") from exc
        return normalized_type, normalized_expression, None

    if normalized_type == "interval":
        return normalized_type, _interval_to_cron(normalized_expression), None

    if normalized_type == "once":
        run_at = _parse_once_datetime(normalized_expression)
        return normalized_type, None, run_at.isoformat()

    raise HTTPException(status_code=400, detail=f"Unsupported schedule type: {schedule_type}")


def _build_schedule_entry(
    *,
    schedule_id: str,
    name: str,
    instruction: str,
    schedule_type: str,
    expression: str,
    timezone_name: str | None,
    preference_timezone: str,
    enabled: bool,
    created_at: str,
) -> dict:
    normalized_type, cron_expression, scheduled_at = _resolve_schedule_expression(
        schedule_type,
        expression,
    )
    entry = {
        "id": schedule_id,
        "name": name,
        "message": instruction,
        "instruction": instruction,
        "user_request": name,
        "schedule": scheduled_at or expression,
        "schedule_type": normalized_type,
        "timezone": valid_timezone_name(timezone_name, preference_timezone),
        "enabled": enabled,
        # The runtime scheduler only honours ``paused``; keep it in sync with the
        # UI's ``enabled`` flag so a paused schedule actually stops firing.
        "paused": not enabled,
        "last_run": None,
        "last_run_status": "idle",
        "created_at": created_at,
        "updated_at": created_at,
    }
    if cron_expression is not None:
        entry["cron_expression"] = cron_expression
    if scheduled_at is not None:
        entry["scheduled_at"] = scheduled_at
    next_run = Scheduler().next_run_for_entry(entry, preference_timezone)
    if next_run:
        entry["next_run"] = _next_run_iso(next_run)
        entry["next_run_at"] = _next_run_iso(next_run)
    return entry


def _patch_document_entry(
    *,
    entries: dict,
    entry_id: str,
    apply: Callable[[dict], None],
    save: Callable[[], None],
    not_found_detail: str = "Widget not found",
) -> dict:
    """Mutate one dict-keyed entry in place, persist it, and return it.

    ``entries`` is the already-read whole document (or the id-keyed
    sub-document, e.g. mcp's ``mcpServers``); ``save`` persists whatever
    container ``entries`` belongs to - the field-level mutation itself stays a
    per-widget-type closure, since that logic genuinely differs per type.
    """
    entry = entries.get(entry_id)
    if not isinstance(entry, dict):
        raise HTTPException(status_code=404, detail=not_found_detail)
    apply(entry)
    save()
    return entry


def _delete_document_entry(*, entries: dict, entry_id: str, save: Callable[[], None]) -> bool:
    """Delete one dict-keyed entry if present, persist, and report whether it existed."""
    if not isinstance(entries.get(entry_id), dict):
        return False
    del entries[entry_id]
    save()
    return True


def _widget_or_fallback(
    widget_id: str, state_reader: monitoring_state.StateReader, build_fallback: Callable[[], dict]
) -> dict:
    """Return the freshly-computed widget if ``get_widgets()`` already reflects the
    write, else a caller-supplied synthetic stand-in (cache staleness fallback)."""
    refreshed = next((w for w in state_reader.get_widgets() if w["id"] == widget_id), None)
    return refreshed if refreshed is not None else build_fallback()


_RECENT_SCHEDULE_RUNS = 10


def _schedule_runs_by_schedule(
    state_reader: monitoring_state.StateReader,
) -> dict[str, list[schedules_models.ScheduleRunInfo]]:
    """Return each schedule's latest read-only run views, newest first."""
    store = state_reader.document_store
    document = store.get_document(storage_documents.PROGENA_STATE_PREFIX, "schedule_runs", [])
    payload = document.payload if document is not None else []
    records = (
        [record for record in payload if isinstance(record, dict)]
        if isinstance(payload, list)
        else []
    )
    records.sort(
        key=lambda record: str(
            record.get("started_at") or record.get("created_at") or record.get("updated_at") or ""
        ),
        reverse=True,
    )
    live = runtime_liveness.live_runtime(store)
    by_schedule: dict[str, list[schedules_models.ScheduleRunInfo]] = {}
    for record in records:
        runs = by_schedule.setdefault(str(record.get("schedule_id") or ""), [])
        if len(runs) >= _RECENT_SCHEDULE_RUNS:
            continue
        status = str(record.get("status") or "queued").lower()
        started_at = record.get("started_at")
        if live is not None and ownership.schedule_run_interrupted(
            record, live.instance_id, live.started_at
        ):
            status = "interrupted"
        runs.append(
            schedules_models.ScheduleRunInfo(
                run_id=str(record.get("run_id") or ""),
                status=status,
                started_at=str(started_at) if started_at is not None else None,
                completed_at=(
                    str(record["completed_at"]) if record.get("completed_at") is not None else None
                ),
                thread_id=str(record.get("thread_id") or ""),
                error=str(record["error"]) if record.get("error") is not None else None,
            )
        )
    return by_schedule


def broadcast_schedule_snapshot(
    *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current schedule list to canonical /api/ui/stream subscribers.

    Emitted after backend-side schedule mutations and whenever the runtime
    reports a schedule change (fire / chat-driven CRUD) via the SSE event
    bridge, so the Schedules page reflects changes live.
    """
    state_reader = monitoring_state.get_state_reader()
    runs = _schedule_runs_by_schedule(state_reader)
    preference_timezone = preferences_store.load_preferences().get("timezone", "UTC")
    items = [
        _schedule_item_to_info(item, runs.get(item.id), preference_timezone).model_dump(mode="json")
        for item in state_reader.get_schedule()
    ]
    return events_stream.broadcast_ui_event(
        "schedule.snapshot", {"items": items}, request_id=request_id
    )


def broadcast_widgets_snapshot(
    items: list[schedules_models.WidgetResponse] | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the unified widget list (dashboard/cross-cutting views).

    Callers holding a freshly built list can pass it in to avoid recomputing
    widgets and to keep the SSE snapshot consistent with their HTTP response.
    """
    if items is None:
        state_reader = monitoring_state.get_state_reader()
        items = [_widget_to_response(widget) for widget in state_reader.get_widgets()]
    payload = [item.model_dump(mode="json") for item in items]
    return events_stream.broadcast_ui_event(
        "widgets.updated", {"items": payload}, request_id=request_id
    )
