"""Backend-owned runs read model and trace reconstruction.

The list path only uses durable metadata. Checkpoint histories are opened only
when a trace is requested, where message usage can be safely deduplicated.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import HTTPException

from src.backend.checkpoint_state import checkpoint_state
from src.backend.runtime_liveness import live_runtime
from src.backend.storage.documents import PROGENA_STATE_PREFIX, store_database_url
from src.backend.workflows import events as workflow_events
from src.progena.runtime.ownership import (
    routine_attempt_interrupted,
    schedule_run_interrupted,
)
from src.progena.workflows.documents import workflow_definition_key, workflow_run_key

from .models import RunKind, RunPeriod, RunRow, RunSort, RunTrace, Span, SpanKind

_PAGE_SIZE = 50
_TERMINAL = frozenset({"completed", "failed", "cancelled", "stopped", "error", "interrupted"})
_CURSOR_VERSION = 3


@dataclass(frozen=True)
class _RunsCursor:
    since: str | None
    as_of: str
    duration_us: int | None
    started_at: str | None
    row_kind: RunKind
    run_id: str


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _time(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )


def _status(value: object, default: str = "queued") -> str:
    return _text(value).lower() or default


def _now() -> datetime:
    return datetime.now(UTC)


def _period_since(period: RunPeriod | None, now: datetime) -> str | None:
    if period is None:
        return None
    windows = {
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(days=7),
        "month": timedelta(days=30),
    }
    return (now - windows[period]).astimezone(UTC).isoformat(timespec="microseconds")


def _token_total(value: object) -> int | None:
    """Return a recorded total without converting unavailable usage to zero."""
    if not isinstance(value, Mapping):
        return None
    total = value.get("total_tokens")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return int(total)
    input_tokens, output_tokens = value.get("input_tokens"), value.get("output_tokens")
    if (
        isinstance(input_tokens, (int, float))
        and not isinstance(input_tokens, bool)
        and isinstance(output_tokens, (int, float))
        and not isinstance(output_tokens, bool)
    ):
        return int(input_tokens + output_tokens)
    return None


def _span_kind(node_kind: str) -> SpanKind:
    if node_kind == "agent":
        return "agent"
    if node_kind == "routine":
        return "routine"
    return "step"


def _entry_end(start: str, entry: Mapping[str, Any]) -> str | None:
    end = _time(entry.get("completed_at"))
    duration = entry.get("duration_ms")
    if end is None and isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return (datetime.fromisoformat(start) + timedelta(milliseconds=duration)).isoformat(
            timespec="microseconds"
        )
    return end


def _attempt_window(
    run_id: str,
    event: Mapping[str, Any],
    latest: Mapping[str, Any],
    started: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Bound one request's work when a routine thread has been resumed."""
    started_event = started.get(run_id)
    begin = _time(started_event.get("timestamp")) if isinstance(started_event, Mapping) else None
    if begin is None and _status(event.get("status")) == "started":
        begin = _time(event.get("timestamp"))
    thread_id = _text(event.get("thread_id"))
    if begin is None or not thread_id:
        return begin, None
    later = [
        candidate
        for request_id, candidate_event in latest.items()
        if request_id != run_id
        and isinstance(candidate_event, Mapping)
        and _text(candidate_event.get("thread_id")) == thread_id
        and isinstance(candidate_started := started.get(request_id), Mapping)
        and (candidate := _time(candidate_started.get("timestamp"))) is not None
        and candidate > begin
    ]
    return begin, min(later) if later else None


def _within_window(when: str | None, start: str | None, end: str | None) -> bool:
    """A missing timestamp remains visible so the established validation can report it."""
    return when is None or ((start is None or when >= start) and (end is None or when < end))


def _intersect_windows(
    start: str | None,
    end: str | None,
    limit_start: str | None,
    limit_end: str | None,
) -> tuple[str | None, str | None]:
    """Intersect a child execution's attempt with its owning node's lifetime."""
    return (
        max(value for value in (start, limit_start) if value is not None)
        if start is not None or limit_start is not None
        else None,
        min(value for value in (end, limit_end) if value is not None)
        if end is not None or limit_end is not None
        else None,
    )


def _overview_root(row: RunRow) -> list[Span]:
    if row.started_at is None:
        return []
    return [
        Span(
            id=f"{row.kind}:{row.run_id}",
            kind="run",
            name=row.name,
            operation=row.trigger,
            start=row.started_at,
            end=row.completed_at,
            status=row.status,
            tokens=row.tokens,
        )
    ]


def _rollup_tokens(spans: Iterable[Span]) -> int | None:
    """Use leaf measurements before their aggregate structural metadata."""
    items = list(spans)
    by_parent: dict[str | None, list[Span]] = {}
    ids = {span.id for span in items}
    for span in items:
        by_parent.setdefault(span.parent_id if span.parent_id in ids else None, []).append(span)

    def visit(span: Span) -> int | None:
        child_amounts = [
            amount for child in by_parent.get(span.id, []) if (amount := visit(child)) is not None
        ]
        if child_amounts:
            return sum(child_amounts)
        return span.tokens

    amounts = [amount for span in by_parent.get(None, []) if (amount := visit(span)) is not None]
    return sum(amounts) if amounts else None


def _workflow_overview(
    row: RunRow, payload: Mapping[str, Any], nodes: Mapping[str, Any]
) -> list[Span]:
    spans = _overview_root(row)
    parent_id = spans[0].id if spans else f"workflow:{row.run_id}"
    audit = payload.get("step_audit")
    if not isinstance(audit, list):
        return spans
    for index, entry in enumerate(audit):
        if not isinstance(entry, Mapping):
            continue
        start = _time(entry.get("started_at"))
        if start is None:
            continue
        node_id = _text(entry.get("node_id"))
        node = nodes.get(node_id)
        node = node if isinstance(node, Mapping) else {}
        node_kind = _text(node.get("kind")) or _text(entry.get("kind"))
        end = _time(entry.get("completed_at"))
        spans.append(
            Span(
                id=f"workflow:{row.run_id}:step:{index}",
                parent_id=parent_id,
                kind=_span_kind(node_kind),
                name=_text(node.get("title")) or node_id or "Workflow step",
                operation=node_kind or None,
                start=start,
                end=end,
                status=_status(entry.get("status"), "completed" if end else "running"),
                tokens=_token_total(entry.get("tokens")),
                timing_source="step_audit",
            )
        )
    return spans


def _duration_us(row: RunRow, as_of: str) -> int | None:
    if row.started_at is None:
        return None
    end = row.completed_at or as_of
    try:
        elapsed = max(
            datetime.fromisoformat(end) - datetime.fromisoformat(row.started_at), timedelta()
        )
    except ValueError:
        return None
    return elapsed.days * 86_400_000_000 + elapsed.seconds * 1_000_000 + elapsed.microseconds


def _order_key(
    *,
    sort: RunSort,
    duration_us: int | None,
    started_at: str | None,
    kind: RunKind,
    run_id: str,
) -> tuple[Any, ...]:
    if sort == "oldest":
        return (started_at is None, started_at or "", kind, run_id)
    if sort == "duration":
        return (
            duration_us is not None,
            duration_us if duration_us is not None else -1,
            started_at is not None,
            started_at or "",
            kind,
            run_id,
        )
    if sort == "shortest":
        return (
            duration_us is None,
            duration_us if duration_us is not None else 0,
            started_at is None,
            started_at or "",
            kind,
            run_id,
        )
    return (started_at is not None, started_at or "", kind, run_id)


def _row_order_key(row: RunRow, sort: RunSort, as_of: str) -> tuple[Any, ...]:
    return _order_key(
        sort=sort,
        duration_us=_duration_us(row, as_of) if sort in {"duration", "shortest"} else None,
        started_at=row.started_at,
        kind=row.kind,
        run_id=row.run_id,
    )


def _sort_rows(rows: Iterable[RunRow], sort: RunSort, as_of: str) -> list[RunRow]:
    return sorted(
        rows,
        key=lambda row: _row_order_key(row, sort, as_of),
        reverse=sort not in {"oldest", "shortest"},
    )


def _rows_after_cursor(
    rows: Iterable[RunRow], cursor: _RunsCursor | None, sort: RunSort, as_of: str
) -> list[RunRow]:
    if cursor is None:
        return list(rows)
    boundary = _order_key(
        sort=sort,
        duration_us=cursor.duration_us if sort in {"duration", "shortest"} else None,
        started_at=cursor.started_at,
        kind=cursor.row_kind,
        run_id=cursor.run_id,
    )
    if sort in {"oldest", "shortest"}:
        return [row for row in rows if _row_order_key(row, sort, as_of) > boundary]
    return [row for row in rows if _row_order_key(row, sort, as_of) < boundary]


def _query_scope(
    *, sort: RunSort, kind: RunKind | None, status: str, q: str, period: RunPeriod | None
) -> str:
    encoded = json.dumps([sort, kind, status, q, period], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _cursor_encode(
    row: RunRow,
    *,
    sort: RunSort,
    kind: RunKind | None,
    status: str,
    q: str,
    period: RunPeriod | None,
    since: str | None,
    as_of: str,
) -> str:
    value = {
        "v": _CURSOR_VERSION,
        "scope": _query_scope(sort=sort, kind=kind, status=status, q=q, period=period),
        "since": since,
        "as_of": as_of,
        "key": [
            _duration_us(row, as_of) if sort in {"duration", "shortest"} else None,
            row.started_at,
            row.kind,
            row.run_id,
        ],
    }
    return (
        base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        )
        .decode()
        .rstrip("=")
    )


def _cursor_decode(
    cursor: str | None,
    *,
    sort: RunSort,
    kind: RunKind | None,
    status: str,
    q: str,
    period: RunPeriod | None,
) -> _RunsCursor | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(value, dict) or value.get("v") != _CURSOR_VERSION:
            raise ValueError
        scope = value.get("scope")
        raw_since = value.get("since")
        as_of = _time(value.get("as_of"))
        key = value.get("key")
        valid_kinds = {"routine", "workflow", "schedule"}
        if (
            not isinstance(scope, str)
            or len(scope) != 64
            or any(character not in "0123456789abcdef" for character in scope)
            or raw_since is not None
            and not isinstance(raw_since, str)
            or (period is None) != (raw_since is None)
            or as_of is None
            or not isinstance(key, list)
            or len(key) != 4
        ):
            raise ValueError
        duration_us, raw_started_at, row_kind, run_id = key
        started_at = _time(raw_started_at) if raw_started_at is not None else None
        since = _time(raw_since) if raw_since is not None else None
        if (
            raw_started_at is not None
            and started_at is None
            or raw_since is not None
            and since is None
            or duration_us is not None
            and (
                not isinstance(duration_us, int)
                or isinstance(duration_us, bool)
                or duration_us < 0
                or raw_started_at is None
            )
            or row_kind not in valid_kinds
            or not isinstance(run_id, str)
            or not run_id
        ):
            raise ValueError
        decoded = _RunsCursor(
            since=since,
            as_of=as_of,
            duration_us=cast(int | None, duration_us),
            started_at=started_at,
            row_kind=cast(RunKind, row_kind),
            run_id=run_id,
        )
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid runs cursor") from None
    if scope != _query_scope(sort=sort, kind=kind, status=status, q=q, period=period):
        raise HTTPException(status_code=400, detail="Runs cursor does not match query")
    return decoded


def _workflow_definition(store: Any, workflow_id: str) -> dict[str, Any]:
    document = store.get_document(PROGENA_STATE_PREFIX, workflow_definition_key(workflow_id), None)
    payload = getattr(document, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _workflow_row(
    store: Any, payload: Mapping[str, Any], definition: dict[str, Any] | None = None
) -> RunRow:
    if definition is None:
        definition = _workflow_definition(store, _text(payload.get("workflow_id")))
    nodes = (
        definition.get("graph", {}).get("nodes", {})
        if isinstance(definition.get("graph"), dict)
        else {}
    )
    status = _status(payload.get("status"))
    row = RunRow(
        kind="workflow",
        run_id=_text(payload.get("run_id")),
        name=_text(definition.get("title")) or _text(payload.get("workflow_id")) or "Workflow",
        trigger=_text(payload.get("source")) or "manual",
        status=status,
        started_at=_time(payload.get("started_at")) or _time(payload.get("queued_at")),
        completed_at=_time(payload.get("completed_at")) if status in _TERMINAL else None,
        tokens=_token_total(payload.get("tokens")),
    )
    row.overview = _workflow_overview(row, payload, nodes)
    node_threads = payload.get("node_threads")
    if not isinstance(node_threads, Mapping):
        outputs = payload.get("node_outputs")
        node_threads = (
            {
                node_id: {"thread_id": output["thread_id"]}
                for node_id, output in outputs.items()
                if isinstance(output, Mapping) and isinstance(output.get("thread_id"), str)
            }
            if isinstance(outputs, Mapping)
            else {}
        )
    pending = payload.get("pending_routine")
    pending = pending if isinstance(pending, Mapping) else {}
    audit = payload.get("step_audit")
    audit = audit if isinstance(audit, list) else []
    spans = {span.id: span for span in row.overview}
    seen_threads: set[str] = set()
    for index in range(len(audit) - 1, -1, -1):
        entry = audit[index]
        if not isinstance(entry, Mapping):
            continue
        step = spans.get(f"workflow:{row.run_id}:step:{index}")
        if step is None or step.kind != "routine":
            continue
        node_id = _text(entry.get("node_id"))
        reference = node_threads.get(node_id)
        thread_id = _text(reference.get("thread_id")) if isinstance(reference, Mapping) else ""
        if not thread_id and _text(pending.get("node_id")) == node_id:
            thread_id = _text(pending.get("thread_id"))
        if thread_id and thread_id not in seen_threads:
            row._routine_threads[step.id] = thread_id
            seen_threads.add(thread_id)
    row.tokens = _rollup_tokens(row.overview)
    return row


def _routine_overview(
    row: RunRow,
    graph: Mapping[str, Any],
    meta: Mapping[str, Any],
    window_start: str | None = None,
    window_end: str | None = None,
    *,
    inclusive_end: bool = False,
) -> list[Span]:
    spans = _overview_root(row)
    parent_id = spans[0].id if spans else f"routine:{row.run_id}"
    nodes = {
        _text(node.get("id")): node
        for node in graph.get("nodes", [])
        if isinstance(node, Mapping) and _text(node.get("id"))
    }
    records = [
        (node_id, index, entry)
        for node_id, entries in meta.items()
        if isinstance(entries, list)
        for index, entry in enumerate(entries)
        if isinstance(entry, Mapping)
        and (
            _within_window(_time(entry.get("started_at")), window_start, window_end)
            or (
                inclusive_end
                and window_end is not None
                and _time(entry.get("started_at")) == window_end
            )
        )
    ]
    records.sort(
        key=lambda record: (
            record[2].get("step") if isinstance(record[2].get("step"), int) else 0,
            _text(record[2].get("started_at")),
        )
    )
    for node_id, index, entry in records:
        start = _time(entry.get("started_at"))
        if start is None:
            continue
        node = nodes.get(_text(node_id), {})
        node_kind = _text(node.get("kind"))
        end = _entry_end(start, entry)
        if window_end is not None and (end is None or end > window_end):
            end = window_end
        error = _text(entry.get("error"))
        spans.append(
            Span(
                id=f"routine:{row.run_id}:node:{node_id}:{index}",
                parent_id=parent_id,
                kind=_span_kind(node_kind),
                name=_text(node.get("label")) or _text(node_id),
                operation=node_kind or None,
                start=start,
                end=end,
                status="failed"
                if error
                else _status(entry.get("status"), "completed" if end else "running"),
                tokens=_token_total(entry.get("tokens")),
                timing_source="run_meta",
            )
        )
    return spans


async def _routine_rows(
    store: Any, run_id: str | None = None, *, since: str | None = None
) -> list[RunRow]:
    await workflow_events._lifecycle_index.ensure_loaded(store)
    latest = workflow_events._lifecycle_index.latest
    payloads = [latest[run_id]] if run_id in latest else [] if run_id else list(latest.values())
    started_events = dict(workflow_events._lifecycle_index.started)
    return await asyncio.to_thread(_routine_metadata_rows, store, payloads, started_events, since)


def _routine_metadata_rows(
    store: Any,
    payloads: list[Any],
    started_events: dict[str, Any],
    since: str | None = None,
) -> list[RunRow]:
    live = live_runtime(store)
    rows: list[RunRow] = []
    graphs: dict[tuple[str, str], Any] = {}
    for payload in payloads:
        if (
            not isinstance(payload, dict)
            or payload.get("parent")
            or _text(payload.get("session_id")).startswith("workflow_run_")
        ):
            continue
        request_id = _text(payload.get("request_id"))
        if not request_id:
            continue
        started = started_events.get(request_id)
        started_at = _time((started or payload).get("timestamp"))
        if since is not None and (started_at is None or started_at < since):
            continue
        status = _status(payload.get("status"))
        if routine_attempt_interrupted(
            status, started, getattr(live, "instance_id", None), getattr(live, "started_at", None)
        ):
            status = "interrupted"
        elif status == "enqueued":
            status = "queued"
        elif status in {"started", "stopping", "cancelling"}:
            status = "running"
        routine_id, revision_id = _routine_identity(_text(payload.get("thread_id")), payload)
        identity = (routine_id, revision_id)
        if identity not in graphs:
            graphs[identity] = (
                store.get_routine_revision(routine_id, revision_id)
                if routine_id and revision_id
                else None
            )
        graph = graphs[identity]
        graph = graph if isinstance(graph, dict) else {}
        row = RunRow(
            kind="routine",
            run_id=request_id,
            name=_text(graph.get("name")) or _text(payload.get("name")) or routine_id or "Routine",
            trigger=_text(payload.get("trigger")) or "manual",
            status=status,
            started_at=started_at,
            completed_at=_time(payload.get("timestamp")) if status in _TERMINAL else None,
        )
        row.overview = _overview_root(row)
        rows.append(row)
    return rows


async def _workflow_rows(
    store: Any,
    *,
    limit: int,
    status: str,
    q: str,
    sort: RunSort,
    cursor: _RunsCursor | None,
    since: str | None,
    as_of: str,
) -> list[RunRow]:
    records = await asyncio.to_thread(
        store.query_workflow_run_page,
        status=status,
        q=q,
        sort=sort,
        cursor=(
            (cursor.duration_us, cursor.started_at, cursor.row_kind, cursor.run_id)
            if cursor is not None
            else None
        ),
        since=since,
        as_of=as_of,
        limit=limit,
    )
    return [_workflow_row(store, payload, definition) for payload, definition in records]


def _schedule_rows(store: Any, *, since: str | None = None) -> list[RunRow]:
    document = store.get_document(PROGENA_STATE_PREFIX, "schedule_runs", None)
    records = getattr(document, "payload", None)
    if not isinstance(records, list):
        return []
    schedule_document = store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
    schedules = getattr(schedule_document, "payload", None)
    schedules = schedules if isinstance(schedules, dict) else {}
    live = live_runtime(store)
    rows: list[RunRow] = []
    for payload in records:
        if not isinstance(payload, dict) or not _text(payload.get("run_id")):
            continue
        started_at = _time(payload.get("started_at")) or _time(payload.get("created_at"))
        if since is not None and (started_at is None or started_at < since):
            continue
        status = _status(payload.get("status"))
        if schedule_run_interrupted(
            payload, getattr(live, "instance_id", None), getattr(live, "started_at", None)
        ):
            status = "interrupted"
        elif status == "error":
            status = "failed"
        schedule_id = _text(payload.get("schedule_id"))
        definition = schedules.get(schedule_id, {}) if isinstance(schedules, dict) else {}
        name = (
            (_text(definition.get("name")) or _text(definition.get("title")))
            if isinstance(definition, dict)
            else ""
        )
        row = RunRow(
            kind="schedule",
            run_id=_text(payload.get("run_id")),
            name=name or schedule_id or "Schedule",
            trigger="schedule",
            status=status,
            started_at=started_at,
            completed_at=_time(payload.get("completed_at")) if status in _TERMINAL else None,
            tokens=_token_total(payload.get("tokens")),
        )
        row.overview = _overview_root(row)
        if row.overview:
            row.overview[0].summary = _text(payload.get("user_request"))[:400] or None
        rows.append(row)
    return rows


async def list_runs(
    store: Any,
    *,
    kind: RunKind | None = None,
    status: str | None = None,
    q: str | None = None,
    period: RunPeriod | None = None,
    sort: RunSort = "newest",
    cursor: str | None = None,
    limit: int = _PAGE_SIZE,
) -> tuple[list[RunRow], str | None]:
    """Return a globally sorted lightweight page with a query-bound keyset cursor."""
    needle = (q or "").strip().lower()
    desired_status = (status or "").strip().lower()
    requested = _cursor_decode(
        cursor,
        sort=sort,
        kind=kind,
        status=desired_status,
        q=needle,
        period=period,
    )
    now = _now().astimezone(UTC)
    as_of = requested.as_of if requested is not None else now.isoformat(timespec="microseconds")
    since = requested.since if requested is not None else _period_since(period, now)
    groups: list[Iterable[RunRow]] = []
    if kind in (None, "workflow"):
        groups.append(
            await _workflow_rows(
                store,
                limit=limit + 1,
                status=desired_status,
                q=needle,
                sort=sort,
                cursor=requested,
                since=since,
                as_of=as_of,
            )
        )
    if kind in (None, "schedule"):
        groups.append(await asyncio.to_thread(_schedule_rows, store, since=since))
    if kind in (None, "routine"):
        groups.append(await _routine_rows(store, since=since))
    rows = [
        row
        for group in groups
        for row in group
        if (not desired_status or row.status.lower() == desired_status)
        and (
            not needle
            or needle in row.name.lower()
            or needle in row.trigger.lower()
            or needle in row.run_id.lower()
        )
        and (since is None or (row.started_at is not None and row.started_at >= since))
    ]
    rows = _rows_after_cursor(rows, requested, sort, as_of)
    rows = _sort_rows(rows, sort, as_of)
    page = rows[:limit]
    await _hydrate_run_overviews(store, page)
    return (
        page,
        (
            _cursor_encode(
                page[-1],
                sort=sort,
                kind=kind,
                status=desired_status,
                q=needle,
                period=period,
                since=since,
                as_of=as_of,
            )
            if len(rows) > limit and page
            else None
        ),
    )


async def get_run_row(store: Any, kind: RunKind, run_id: str) -> RunRow | None:
    """Look up one run with only its selected durable checkpoint tip."""
    if kind == "workflow":
        document = await asyncio.to_thread(
            store.get_document, PROGENA_STATE_PREFIX, workflow_run_key(run_id), None
        )
        payload = getattr(document, "payload", None)
        if not isinstance(payload, dict):
            return None
        row = await asyncio.to_thread(_workflow_row, store, payload)
        await _hydrate_run_overviews(store, [row])
        return row
    if kind == "schedule":
        return next(
            (row for row in await asyncio.to_thread(_schedule_rows, store) if row.run_id == run_id),
            None,
        )
    rows = await _routine_rows(store, run_id)
    await _hydrate_run_overviews(store, rows)
    return rows[0] if rows else None


async def checkpoint_row_changed(store: Any, thread_id: str) -> list[RunRow]:
    """Resolve durable routine lifecycle IDs from the checkpoint thread."""
    await workflow_events._lifecycle_index.ensure_loaded(store)
    run_ids = {
        run_id
        for run_id, event in workflow_events._lifecycle_index.latest.items()
        if isinstance(event, dict) and _text(event.get("thread_id")) == thread_id
    }
    rows = [row for row in await _routine_rows(store) if row.run_id in run_ids]
    await _hydrate_run_overviews(store, rows)
    return rows


@asynccontextmanager
async def _trace_saver(store: Any):
    """Open one saver for every checkpoint-backed span in one trace."""
    database_url = store_database_url(store)
    if not database_url:
        yield None
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    from src.backend.storage.documents import ensure_runs_checkpoint_trigger

    await asyncio.to_thread(ensure_runs_checkpoint_trigger, store)

    async with AsyncPostgresSaver.from_conn_string(
        database_url, serde=JsonPlusSerializer(pickle_fallback=False)
    ) as saver:
        yield saver


async def _tip_run_meta(saver: Any, thread_id: str) -> Mapping[str, Any] | None:
    tip = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
    values = tip.checkpoint.get("channel_values", {}) if tip is not None else {}
    meta = values.get("run_meta", {}) if isinstance(values, dict) else {}
    return meta if isinstance(meta, Mapping) else None


async def _routine_graph(
    store: Any, thread_id: str, event: object | None = None
) -> Mapping[str, Any]:
    routine_id, revision_id = _routine_identity(thread_id, event)
    graph = (
        await asyncio.to_thread(store.get_routine_revision, routine_id, revision_id)
        if routine_id and revision_id
        else {}
    )
    return graph if isinstance(graph, Mapping) else {}


async def _hydrate_run_overviews(store: Any, rows: list[RunRow]) -> None:
    """Hydrate selected root tips and workflow routine children without history reads."""
    routines = {row.run_id: row for row in rows if row.kind == "routine"}
    workflows = [row for row in rows if row.kind == "workflow" and row._routine_threads]
    if not routines and not workflows:
        return
    await workflow_events._lifecycle_index.ensure_loaded(store)
    async with _trace_saver(store) as saver:
        if saver is None:
            return
        graphs: dict[str, Mapping[str, Any]] = {}
        metas: dict[str, Mapping[str, Any] | None] = {}
        for run_id, row in routines.items():
            event = workflow_events._lifecycle_index.latest.get(run_id)
            if not isinstance(event, Mapping):
                continue
            thread_id = _text(event.get("thread_id"))
            if not thread_id:
                continue
            if thread_id not in metas:
                metas[thread_id] = await _tip_run_meta(saver, thread_id)
            meta = metas[thread_id]
            if meta is None:
                continue
            if thread_id not in graphs:
                graphs[thread_id] = await _routine_graph(store, thread_id, event)
            window_start, window_end = _attempt_window(
                run_id,
                event,
                workflow_events._lifecycle_index.latest,
                workflow_events._lifecycle_index.started,
            )
            row.overview = _routine_overview(row, graphs[thread_id], meta, window_start, window_end)
            row.tokens = _rollup_tokens(row.overview)
        for row in workflows:
            owners = {span.id: span for span in row.overview}
            for owner_id, thread_id in row._routine_threads.items():
                owner = owners.get(owner_id)
                if owner is None:
                    continue
                if thread_id not in metas:
                    metas[thread_id] = await _tip_run_meta(saver, thread_id)
                meta = metas[thread_id]
                if meta is None:
                    continue
                if thread_id not in graphs:
                    graphs[thread_id] = await _routine_graph(store, thread_id)
                child = RunRow(
                    kind="routine",
                    run_id=thread_id,
                    name=owner.name,
                    trigger="workflow",
                    status=owner.status,
                    started_at=owner.start,
                    completed_at=owner.end,
                )
                for span in _routine_overview(
                    child,
                    graphs[thread_id],
                    meta,
                    owner.start,
                    owner.end,
                    inclusive_end=True,
                )[1:]:
                    span.parent_id = owner.id
                    row.overview.append(span)
            row.tokens = _rollup_tokens(row.overview)


def _message_value(raw: object, name: str) -> object:
    return raw.get(name) if isinstance(raw, dict) else getattr(raw, name, None)


def _value_summary(value: object, depth: int = 0) -> str:
    """Bound detail payloads without dumping nested tool/document data."""
    if value is None:
        return "Not recorded"
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _value_summary(json.loads(text), depth)
            except ValueError:
                pass
        return text if len(text) <= 400 else f"{text[:180]}\n… [abbreviated] …\n{text[-180:]}"
    if isinstance(value, dict):
        if depth:
            return f"{len(value)} fields"
        fields = []
        for index, (key, item) in enumerate(value.items()):
            if index == 6:
                fields.append(f"… {len(value) - index} more fields")
                break
            fields.append(f"{key}: {_value_summary(item, depth + 1)}")
        return "\n".join(fields) or "Empty object"
    if isinstance(value, list):
        texts = [
            item["text"]
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        return _value_summary("\n".join(texts), depth) if texts else f"{len(value)} items"
    return str(value)


def _conversation_spans(
    store: Any,
    parent_id: str,
    thread: Any,
    seen_messages: set[str],
    window_start: str | None = None,
    window_end: str | None = None,
) -> tuple[list[Span], int]:
    """Use shared history reconstruction, including checkpoint-write message deltas."""
    spans: list[Span] = []
    tokens = 0
    calls: dict[str, tuple[str, str | None]] = {}
    tool_statuses: dict[str, tuple[str, str | None]] = {}
    tool_inputs: dict[str, str] = {}
    tool_outputs: dict[str, str] = {}
    previous: str | None = None
    for message in thread.messages:
        message_id = _text(_message_value(message, "id"))
        committed = thread.committed_at.get(message_id) if message_id else None
        at = _time(committed.isoformat()) if committed is not None else previous
        message_type = _text(_message_value(message, "type"))
        raw_calls = _message_value(message, "tool_calls")
        inside = bool(at and _within_window(at, window_start, window_end))
        if inside:
            for call in raw_calls if isinstance(raw_calls, list) else []:
                if isinstance(call, dict) and _text(call.get("id")):
                    calls[_text(call["id"])] = (_text(call.get("name")) or "Tool", at)
                    tool_inputs[_text(call["id"])] = _value_summary(call.get("args"))
        tool_call_id = _text(_message_value(message, "tool_call_id"))
        if tool_call_id:
            status = _text(_message_value(message, "status"))
            error = _text(_message_value(message, "content")) if status == "error" else None
            tool_statuses[tool_call_id] = (status, error)
            tool_outputs[tool_call_id] = _value_summary(_message_value(message, "content"))
        if at and not _within_window(at, window_start, window_end):
            previous = at
            continue
        usage = _message_value(message, "usage_metadata")
        if message_type == "ai" and message_id and message_id not in seen_messages and at:
            seen_messages.add(message_id)
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if isinstance(usage, dict) and total is None:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    total = input_tokens + output_tokens
            amount = int(total) if isinstance(total, (int, float)) else None
            if amount is not None:
                tokens += amount
            metadata = _message_value(message, "response_metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            spans.append(
                Span(
                    id=f"{parent_id}:model:{message_id}",
                    parent_id=parent_id,
                    kind="model",
                    name=_text(metadata.get("model_name"))
                    or _text(metadata.get("model"))
                    or "Model response",
                    operation=_text(metadata.get("model_provider")) or "model",
                    start=max(previous or at, window_start or previous or at),
                    end=at,
                    status="completed",
                    tokens=amount,
                    timing_source="checkpoint-derived",
                    summary=_value_summary(_message_value(message, "content")),
                )
            )
        previous = at or previous
    audits = store.query_tool_audits(calls) if calls else {}
    for tool_id, audit in audits.items():
        timestamp, duration = audit.get("timestamp"), audit.get("duration_ms")
        if not isinstance(timestamp, (int, float)) or not isinstance(duration, (int, float)):
            continue
        end = datetime.fromtimestamp(float(timestamp), UTC)
        start = datetime.fromtimestamp(float(timestamp) - float(duration) / 1000, UTC)
        if window_start is not None:
            start = max(start, datetime.fromisoformat(window_start))
        if window_end is not None:
            end = min(end, datetime.fromisoformat(window_end))
        if start >= end:
            continue
        message_status, message_error = tool_statuses.get(tool_id, ("", None))
        failed = audit.get("success") is False or message_status == "error"
        spans.append(
            Span(
                id=f"{parent_id}:tool:{tool_id}",
                parent_id=parent_id,
                kind="tool",
                name=_text(audit.get("tool_name")) or calls[tool_id][0],
                operation="tool audit",
                start=start.isoformat(),
                end=end.isoformat(),
                status="failed" if failed else "completed",
                summary=f"Input\n{tool_inputs[tool_id]}\n\nOutput\n{tool_outputs.get(tool_id, 'Not recorded')}",
                error=message_error or _text(audit.get("error")) or None,
                timing_source="tool-audit-derived",
            )
        )
    measured = {span.id for span in spans if span.kind == "tool"}
    for tool_id, (name, declared_at) in calls.items():
        if f"{parent_id}:tool:{tool_id}" in measured or not declared_at:
            continue
        message_status, message_error = tool_statuses.get(tool_id, ("", None))
        status = (
            "failed"
            if message_status == "error"
            else "completed"
            if tool_id in tool_statuses
            else "declared"
        )
        spans.append(
            Span(
                id=f"{parent_id}:tool:{tool_id}",
                parent_id=parent_id,
                kind="tool",
                name=name,
                operation="tool declaration; timing unavailable (checkpoint commit marker)",
                start=declared_at,
                end=declared_at,
                status=status,
                error=message_error,
                timing_source="unavailable",
                summary=f"Input\n{tool_inputs[tool_id]}\n\nOutput\n{tool_outputs.get(tool_id, 'Not recorded')}",
            )
        )
    return spans, tokens


async def _thread_spans(
    checkpointer: Any,
    store: Any,
    parent_id: str,
    thread_id: str,
    seen_messages: set[str],
    conversations: dict[str, Any],
    window_start: str | None = None,
    window_end: str | None = None,
) -> tuple[list[Span], int]:
    if checkpointer is None or not thread_id:
        return [], 0
    from src.progena.sessions.history import load_conversation

    if thread_id not in conversations:
        conversations[thread_id] = await load_conversation(checkpointer, thread_id)
    conversation = conversations[thread_id]
    if conversation is None:
        return [], 0
    spans, tokens = await asyncio.to_thread(
        _conversation_spans,
        store,
        parent_id,
        conversation.root,
        seen_messages,
        window_start,
        window_end,
    )
    for tool_call_id, subagent in conversation.subagents.items():
        parent_tool = next(
            (span for span in spans if span.id == f"{parent_id}:tool:{tool_call_id}"), None
        )
        if parent_tool is None:
            continue
        child_id = f"{parent_id}:subagent:{tool_call_id}"
        nested, amount = await asyncio.to_thread(
            _conversation_spans, store, child_id, subagent, seen_messages, window_start, window_end
        )
        committed = [_time(value.isoformat()) for value in subagent.committed_at.values()]
        boundaries = [
            value
            for value in committed
            if value is not None and _within_window(value, window_start, window_end)
        ]
        if not boundaries:
            continue
        spans.append(
            Span(
                id=child_id,
                parent_id=parent_tool.id,
                kind="agent",
                name=subagent.nick or "Subagent",
                operation="subagent",
                start=min(boundaries),
                end=max(boundaries) if parent_tool.status in {"completed", "failed"} else None,
                status=parent_tool.status,
                timing_source="checkpoint-derived",
            )
        )
        spans.extend(nested)
        tokens += amount
    return spans, tokens


def _root_span(kind: RunKind, row: RunRow) -> Span:
    if row.started_at is None:
        raise HTTPException(status_code=422, detail="Run has no persisted start time")
    return Span(
        id=f"{kind}:{row.run_id}",
        kind="run",
        name=row.name,
        operation=row.trigger,
        start=row.started_at,
        end=row.completed_at,
        status=row.status,
        tokens=row.tokens,
    )


def _routine_identity(thread_id: str, event: object) -> tuple[str, str]:
    """Pinned routine identity lives in the immutable routine thread ID."""
    values = thread_id.split(":", 3)
    if len(values) == 4 and values[0] == "routine":
        return values[1], values[2]
    payload = event if isinstance(event, dict) else {}
    return _text(payload.get("agent_id")), _text(payload.get("revision_id"))


async def _routine_children(
    checkpointer: Any,
    store: Any,
    parent_id: str,
    thread_id: str,
    seen_messages: set[str],
    conversations: dict[str, Any],
    window_start: str | None = None,
    window_end: str | None = None,
    ancestors: frozenset[str] = frozenset(),
) -> tuple[list[Span], int, set[str]]:
    """Reconstruct measured nodes, pending tasks, and recursively nested executions."""
    threads: set[str] = {thread_id}
    if checkpointer is None or thread_id in ancestors:
        return [], 0, threads
    ancestors = ancestors | {thread_id}
    tip = await checkpointer.aget_tuple(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    )
    values = tip.checkpoint.get("channel_values", {}) if tip is not None else {}
    meta = values.get("run_meta", {}) if isinstance(values, dict) else {}
    routine_id, revision_id = _routine_identity(thread_id, {})
    graph = (
        await asyncio.to_thread(store.get_routine_revision, routine_id, revision_id)
        if revision_id
        else {}
    )
    nodes = (
        {str(node["id"]): node for node in graph.get("nodes", [])}
        if isinstance(graph, dict)
        else {}
    )
    records = (
        [
            (node_id, index, entry)
            for node_id, entries in meta.items()
            for index, entry in enumerate(entries)
            if isinstance(entry, dict)
            and _within_window(_time(entry.get("started_at")), window_start, window_end)
        ]
        if isinstance(meta, dict)
        else []
    )
    state = checkpoint_state(tip)
    if (
        state["exists"]
        and not state["failed"]
        and _within_window(_time(state["updated_at"]), window_start, window_end)
    ):
        for node_id in state["next"]:
            if node_id in nodes:
                records.append(
                    (
                        node_id,
                        len(records),
                        {
                            "started_at": state["updated_at"],
                            "_pending": True,
                            "status": "waiting" if state["waiting"] else "pending",
                        },
                    )
                )
    records.sort(key=lambda row: row[2].get("started_at") or "")
    spans: list[Span] = []
    tokens = 0
    for node_id, index, entry in records:
        start = _time(entry.get("started_at"))
        if start is None:
            raise HTTPException(
                status_code=422, detail=f"Routine node {node_id} has no persisted start time"
            )
        node = nodes.get(node_id, {})
        node_kind = _text(node.get("kind"))
        end = _entry_end(start, entry)
        if window_end is not None and (end is None or end > window_end):
            end = window_end
        error = _text(entry.get("error")) or None
        child_id = f"{parent_id}:node:{node_id}:{index}"
        spans.append(
            Span(
                id=child_id,
                parent_id=parent_id,
                kind=_span_kind(node_kind),
                name=_text(node.get("label")) or node_id,
                operation=node_kind,
                start=start,
                end=end,
                error=error,
                status="failed"
                if error
                else _status(entry.get("status"), "completed" if end else "running"),
                tokens=_token_total(entry.get("tokens")),
                summary=_value_summary(entry["output"]) if "output" in entry else None,
                timing_source="checkpoint-derived" if entry.get("_pending") else "run_meta",
            )
        )
        output = entry.get("output")
        invocation = entry.get("routine")
        child_thread = _text(output.get("thread_id")) if isinstance(output, dict) else ""
        if not child_thread and isinstance(invocation, dict):
            child_thread = _text(invocation.get("thread_id"))
        if not child_thread and node_kind == "routine" and entry.get("_pending"):
            children = [
                event
                for event in workflow_events._lifecycle_index.latest.values()
                if isinstance(event, dict)
                and isinstance(event.get("parent"), dict)
                and event["parent"].get("thread_id") == thread_id
                and event["parent"].get("node_id") == node_id
            ]
            if children:
                latest = max(children, key=lambda event: _text(event.get("timestamp")))
                child_thread = _text(latest.get("thread_id"))
        if not child_thread and node_kind == "agent":
            child_thread = f"routine-agent:{thread_id}:{node_id}"
        if child_thread:
            threads.add(child_thread)
            child_start, child_end = _intersect_windows(start, end, window_start, window_end)
            if child_thread.startswith("routine:"):
                child_request_id = (
                    _text(invocation.get("request_id")) if isinstance(invocation, dict) else ""
                )
                child_event = workflow_events._lifecycle_index.latest.get(child_request_id)
                if isinstance(child_event, Mapping):
                    attempt_start, attempt_end = _attempt_window(
                        child_request_id,
                        child_event,
                        workflow_events._lifecycle_index.latest,
                        workflow_events._lifecycle_index.started,
                    )
                    child_start, child_end = _intersect_windows(
                        attempt_start, attempt_end, child_start, child_end
                    )
                nested, amount, children = await _routine_children(
                    checkpointer,
                    store,
                    child_id,
                    child_thread,
                    seen_messages,
                    conversations,
                    child_start,
                    child_end,
                    ancestors,
                )
                threads.update(children)
            else:
                nested, amount = await _thread_spans(
                    checkpointer,
                    store,
                    child_id,
                    child_thread,
                    seen_messages,
                    conversations,
                    start,
                    end,
                )
            spans.extend(nested)
            tokens += amount
    return spans, tokens, threads


async def build_trace(store: Any, kind: RunKind, run_id: str) -> tuple[RunTrace, set[str]]:
    """Build an on-demand trace using one native checkpoint reader."""
    await workflow_events._lifecycle_index.ensure_loaded(store)
    row = await get_run_row(store, kind, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    root = _root_span(kind, row)
    spans = [root]
    threads: set[str] = set()
    tokens = 0
    seen_messages: set[str] = set()
    conversations: dict[str, Any] = {}
    queued_at: str | None = None
    async with _trace_saver(store) as checkpointer:
        if kind == "workflow":
            document = await asyncio.to_thread(
                store.get_document, PROGENA_STATE_PREFIX, workflow_run_key(run_id), None
            )
            payload = getattr(document, "payload", None)
            if not isinstance(payload, dict):
                raise HTTPException(status_code=404, detail="Run not found")
            queued_at = _time(payload.get("queued_at"))
            definition = await asyncio.to_thread(
                _workflow_definition, store, _text(payload.get("workflow_id"))
            )
            graph = definition.get("graph", {}) if isinstance(definition.get("graph"), dict) else {}
            nodes = graph.get("nodes", {}) if isinstance(graph.get("nodes"), dict) else {}
            outputs = payload.get("node_outputs")
            if not isinstance(outputs, dict):
                outputs = {}
            for index, audit in enumerate(payload.get("step_audit", [])):
                if not isinstance(audit, dict):
                    continue
                node_id = _text(audit.get("node_id"))
                node = nodes.get(node_id, {}) if isinstance(nodes, dict) else {}
                start, end = _time(audit.get("started_at")), _time(audit.get("completed_at"))
                if not start:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Workflow step {node_id} has no persisted start time",
                    )
                step_id = f"workflow:{run_id}:step:{index}"
                node_kind = _text(audit.get("kind")) or (
                    _text(node.get("kind")) if isinstance(node, dict) else ""
                )
                output = audit.get("output")
                summary = _value_summary(output) if output is not None else None
                error = _text(audit.get("error")) or None
                spans.append(
                    Span(
                        id=step_id,
                        parent_id=root.id,
                        kind=_span_kind(node_kind),
                        name=_text(node.get("title")) or node_id,
                        operation=node_kind or None,
                        start=start,
                        end=end,
                        status=_status(audit.get("status"), "completed" if end else "running"),
                        tokens=_token_total(audit.get("tokens")),
                        summary=summary,
                        error=error,
                        timing_source="step_audit",
                    )
                )
                link = outputs.get(node_id)
                child_thread = _text(link.get("thread_id")) if isinstance(link, dict) else ""
                if not child_thread:
                    child_thread = _text(audit.get("thread_id"))
                pending = payload.get("pending_routine")
                if (
                    not child_thread
                    and isinstance(pending, dict)
                    and pending.get("node_id") == node_id
                ):
                    child_thread = _text(pending.get("thread_id"))
                if node_kind in {"agent", "extract"} and not child_thread:
                    child_thread = _text(payload.get("thread_id"))
                if child_thread:
                    if node_kind == "routine":
                        child_spans, amount, child_threads = await _routine_children(
                            checkpointer,
                            store,
                            step_id,
                            child_thread,
                            seen_messages,
                            conversations,
                            start,
                            end,
                        )
                        threads.update(child_threads)
                    else:
                        threads.add(child_thread)
                        child_spans, amount = await _thread_spans(
                            checkpointer,
                            store,
                            step_id,
                            child_thread,
                            seen_messages,
                            conversations,
                            start,
                            end,
                        )
                    spans.extend(child_spans)
                    tokens += amount
            if _text(payload.get("last_error")) and row.status == "failed":
                root.error = _text(payload.get("last_error"))
        elif kind == "schedule":
            document = await asyncio.to_thread(
                store.get_document, PROGENA_STATE_PREFIX, "schedule_runs", None
            )
            records = getattr(document, "payload", None)
            payload = (
                next(
                    (
                        entry
                        for entry in records
                        if isinstance(entry, dict) and _text(entry.get("run_id")) == run_id
                    ),
                    None,
                )
                if isinstance(records, list)
                else None
            )
            if isinstance(payload, dict):
                queued_at = _time(payload.get("created_at"))
                thread_id = _text(payload.get("thread_id"))
                if thread_id:
                    threads.add(thread_id)
                    child_spans, amount = await _thread_spans(
                        checkpointer,
                        store,
                        root.id,
                        thread_id,
                        seen_messages,
                        conversations,
                        row.started_at,
                        row.completed_at,
                    )
                    spans.extend(child_spans)
                    tokens += amount
                root.error = _text(payload.get("error")) or None
                root.summary = _text(payload.get("user_request"))[:400] or None
        else:
            await workflow_events._lifecycle_index.ensure_loaded(store)
            event = workflow_events._lifecycle_index.latest.get(run_id, {})
            window_start, window_end = _attempt_window(
                run_id,
                event,
                workflow_events._lifecycle_index.latest,
                workflow_events._lifecycle_index.started,
            )
            thread_id = _text(event.get("thread_id")) if isinstance(event, dict) else ""
            enqueue = await asyncio.to_thread(
                store.get_document,
                PROGENA_STATE_PREFIX,
                f"routine_run_event:{run_id}:enqueued",
                None,
            )
            queued_at = _time(enqueue.payload.get("timestamp")) if enqueue is not None else None
            if thread_id:
                child_spans, amount, child_threads = await _routine_children(
                    checkpointer,
                    store,
                    root.id,
                    thread_id,
                    seen_messages,
                    conversations,
                    window_start,
                    window_end,
                )
                spans.extend(child_spans)
                threads.update(child_threads)
                tokens += amount
            root.error = _text(event.get("error")) if isinstance(event, dict) else None
    if queued_at and queued_at < root.start:
        spans.append(
            Span(
                id=f"{root.id}:queue",
                parent_id=root.id,
                kind="queue",
                name="Queue wait",
                operation="queued",
                start=queued_at,
                end=root.start,
                status="completed",
            )
        )
        root.start = queued_at
    spans.sort(key=lambda span: (span.start, span.id != root.id))
    tokens = _rollup_tokens(spans)
    return RunTrace(
        kind=kind,
        run_id=run_id,
        name=row.name,
        status=row.status,
        started_at=root.start,
        completed_at=row.completed_at,
        spans=spans,
        tokens=tokens,
    ), threads
