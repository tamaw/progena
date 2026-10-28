"""Backend-owned runs read model and trace reconstruction.

The list path only uses durable metadata. Checkpoint histories are opened only
when a trace is requested, where message usage can be safely deduplicated.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

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

from .models import RunKind, RunRow, RunTrace, Span

_PAGE_SIZE = 50
_TERMINAL = frozenset({"completed", "failed", "cancelled", "stopped", "error", "interrupted"})
_EXECUTABLE_WORKFLOW_KINDS = frozenset(
    {
        "document_read",
        "document_query",
        "document_write",
        "document_create",
        "document_delete",
        "format",
        "unpack",
        "extract",
        "agent",
        "routine",
        "condition",
        "approval",
    }
)


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


def _cursor_encode(row: RunRow) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps([row.started_at or "", row.kind, row.run_id], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def _cursor_decode(cursor: str | None) -> tuple[str, str, str] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            not isinstance(value, list)
            or len(value) != 3
            or not all(isinstance(v, str) for v in value)
        ):
            raise ValueError
        timestamp = _time(value[0])
        if timestamp is None or value[1] not in {"routine", "workflow", "schedule"} or not value[2]:
            raise ValueError
        return timestamp, value[1], value[2]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid runs cursor") from None


def _row_sort_key(row: RunRow) -> tuple[str, str, str]:
    # Descending timestamp, then deterministic kind and id tie-breakers.
    return row.started_at or "", row.kind, row.run_id


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
    executable = {
        node_id
        for node_id, node in nodes.items()
        if isinstance(node, dict) and _text(node.get("kind")) in _EXECUTABLE_WORKFLOW_KINDS
    }
    completed = set(filter(None, (_text(value) for value in payload.get("completed_node_ids", []))))
    status = _status(payload.get("status"))
    return RunRow(
        kind="workflow",
        run_id=_text(payload.get("run_id")),
        name=_text(definition.get("title")) or _text(payload.get("workflow_id")) or "Workflow",
        trigger=_text(payload.get("source")) or "manual",
        status=status,
        started_at=_time(payload.get("started_at")) or _time(payload.get("queued_at")),
        completed_at=_time(payload.get("completed_at")) if status in _TERMINAL else None,
        steps_done=len(completed & executable),
        steps_total=len(executable),
    )


async def _routine_rows(store: Any, run_id: str | None = None) -> list[RunRow]:
    await workflow_events._lifecycle_index.ensure_loaded(store)
    latest = workflow_events._lifecycle_index.latest
    payloads = [latest[run_id]] if run_id in latest else [] if run_id else list(latest.values())
    started_events = dict(workflow_events._lifecycle_index.started)
    return await asyncio.to_thread(_routine_metadata_rows, store, payloads, started_events)


def _routine_metadata_rows(
    store: Any, payloads: list[Any], started_events: dict[str, Any]
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
        status = _status(payload.get("status"))
        if routine_attempt_interrupted(
            status, started, getattr(live, "instance_id", None), getattr(live, "started_at", None)
        ):
            status = "interrupted"
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
        nodes = graph.get("nodes", []) if isinstance(graph.get("nodes"), list) else []
        executable = {
            str(node.get("id"))
            for node in nodes
            if isinstance(node, dict) and _text(node.get("kind")) not in {"data", "trigger"}
        }
        completed_nodes: set[str] = set()
        rows.append(
            RunRow(
                kind="routine",
                run_id=request_id,
                name=_text(graph.get("name"))
                or _text(payload.get("name"))
                or routine_id
                or "Routine",
                trigger=_text(payload.get("trigger")) or "manual",
                status=status,
                started_at=_time((started or payload).get("timestamp")),
                completed_at=_time(payload.get("timestamp")) if status in _TERMINAL else None,
                steps_done=len(completed_nodes & executable),
                steps_total=len(executable),
            )
        )
    rows.sort(key=_row_sort_key, reverse=True)
    return rows


async def _workflow_rows(
    store: Any, *, limit: int, status: str, q: str, before: tuple[str, str, str] | None
) -> list[RunRow]:
    records = await asyncio.to_thread(
        store.query_workflow_run_page, status=status, q=q, before=before, limit=limit
    )
    return [_workflow_row(store, payload, definition) for payload, definition in records]


def _schedule_rows(store: Any) -> list[RunRow]:
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
        rows.append(
            RunRow(
                kind="schedule",
                run_id=_text(payload.get("run_id")),
                name=name or schedule_id or "Schedule",
                trigger="schedule",
                status=status,
                started_at=_time(payload.get("started_at")) or _time(payload.get("created_at")),
                completed_at=_time(payload.get("completed_at")) if status in _TERMINAL else None,
            )
        )
    rows.sort(key=_row_sort_key, reverse=True)
    return rows


async def list_runs(
    store: Any,
    *,
    kind: RunKind | None = None,
    status: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = _PAGE_SIZE,
) -> tuple[list[RunRow], str | None]:
    """Return lightweight top-level rows with a stable, opaque keyset cursor."""
    requested = _cursor_decode(cursor)
    groups: list[Iterable[RunRow]] = []
    needle = (q or "").strip().casefold()
    desired_status = (status or "").strip().casefold()
    if kind in (None, "workflow"):
        groups.append(
            await _workflow_rows(
                store, limit=limit + 1, status=desired_status, q=needle, before=requested
            )
        )
    if kind in (None, "schedule"):
        groups.append(await asyncio.to_thread(_schedule_rows, store))
    if kind in (None, "routine"):
        groups.append(await _routine_rows(store))
    rows = [row for group in groups for row in group]
    rows = [
        row
        for row in rows
        if (not desired_status or row.status.casefold() == desired_status)
        and (
            not needle
            or needle in row.name.casefold()
            or needle in row.trigger.casefold()
            or needle in row.run_id.casefold()
        )
    ]
    rows.sort(key=_row_sort_key, reverse=True)
    if requested:
        rows = [row for row in rows if _row_sort_key(row) < requested]
    page = rows[:limit]
    await _hydrate_routine_progress(store, page)
    return page, (_cursor_encode(page[-1]) if len(rows) > limit and page else None)


async def get_run_row(store: Any, kind: RunKind, run_id: str) -> RunRow | None:
    """Look up one run without broadcasting or hydrating a trace."""
    if kind == "workflow":
        document = await asyncio.to_thread(
            store.get_document, PROGENA_STATE_PREFIX, workflow_run_key(run_id), None
        )
        payload = getattr(document, "payload", None)
        return (
            await asyncio.to_thread(_workflow_row, store, payload)
            if isinstance(payload, dict)
            else None
        )
    if kind == "schedule":
        return next(
            (row for row in await asyncio.to_thread(_schedule_rows, store) if row.run_id == run_id),
            None,
        )
    rows = await _routine_rows(store, run_id)
    await _hydrate_routine_progress(store, rows)
    return rows[0] if rows else None


async def checkpoint_row_changed(store: Any, thread_id: str) -> list[RunRow]:
    """Resolve durable routine lifecycle IDs from the checkpoint thread."""
    await workflow_events._lifecycle_index.ensure_loaded(store)
    for run_id, event in workflow_events._lifecycle_index.latest.items():
        if isinstance(event, dict) and _text(event.get("thread_id")) == thread_id:
            row = await get_run_row(store, "routine", run_id)
            return [row] if row is not None else []
    return []


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


async def _hydrate_routine_progress(store: Any, rows: list[RunRow]) -> None:
    """Hydrate selected routine root tips with one saver, never checkpoint history."""
    wanted = {row.run_id: row for row in rows if row.kind == "routine"}
    if not wanted:
        return
    await workflow_events._lifecycle_index.ensure_loaded(store)
    async with _trace_saver(store) as saver:
        if saver is None:
            return
        for run_id, row in wanted.items():
            event = workflow_events._lifecycle_index.latest.get(run_id)
            thread_id = _text(event.get("thread_id")) if isinstance(event, dict) else ""
            if not thread_id:
                continue
            tip = await saver.aget_tuple(
                {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            )
            values = tip.checkpoint.get("channel_values", {}) if tip is not None else {}
            meta = values.get("run_meta", {}) if isinstance(values, dict) else {}
            if isinstance(meta, dict):
                row.steps_done = sum(
                    1
                    for entries in meta.values()
                    if isinstance(entries, list)
                    and any(
                        isinstance(entry, dict)
                        and isinstance(entry.get("duration_ms"), (int, float))
                        and not entry.get("error")
                        for entry in entries
                    )
                )


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
        inside = bool(
            at and not ((window_start and at < window_start) or (window_end and at > window_end))
        )
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
        if at and ((window_start and at < window_start) or (window_end and at > window_end)):
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
        boundaries = [value for value in committed if value is not None]
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
        ]
        if isinstance(meta, dict)
        else []
    )
    state = checkpoint_state(tip)
    if state["exists"] and not state["failed"]:
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
        duration = entry.get("duration_ms")
        end = _time(entry.get("completed_at"))
        if end is None and isinstance(duration, (int, float)):
            end = (datetime.fromisoformat(start) + timedelta(milliseconds=duration)).isoformat(
                timespec="microseconds"
            )
        error = _text(entry.get("error")) or None
        child_id = f"{parent_id}:node:{node_id}:{index}"
        spans.append(
            Span(
                id=child_id,
                parent_id=parent_id,
                kind="agent"
                if node_kind == "agent"
                else "routine"
                if node_kind == "routine"
                else "step",
                name=_text(node.get("label")) or node_id,
                operation=node_kind,
                start=start,
                end=end,
                error=error,
                status="failed"
                if error
                else _status(entry.get("status"), "completed" if end else "running"),
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
            if child_thread.startswith("routine:"):
                nested, amount, children = await _routine_children(
                    checkpointer,
                    store,
                    child_id,
                    child_thread,
                    seen_messages,
                    conversations,
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
                node_kind = (
                    _text(node.get("kind")) if isinstance(node, dict) else _text(audit.get("kind"))
                )
                output = audit.get("output")
                summary = _value_summary(output) if output is not None else None
                error = _text(audit.get("error")) or None
                spans.append(
                    Span(
                        id=step_id,
                        parent_id=root.id,
                        kind="routine"
                        if node_kind == "routine"
                        else "agent"
                        if node_kind == "agent"
                        else "step",
                        name=_text(node.get("title")) or node_id,
                        operation=node_kind or None,
                        start=start,
                        end=end,
                        status=_status(audit.get("status"), "completed" if end else "running"),
                        summary=summary,
                        error=error,
                    )
                )
                link = outputs.get(node_id)
                child_thread = _text(link.get("thread_id")) if isinstance(link, dict) else ""
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
                            checkpointer, store, step_id, child_thread, seen_messages, conversations
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
        else:
            await workflow_events._lifecycle_index.ensure_loaded(store)
            event = workflow_events._lifecycle_index.latest.get(run_id, {})
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
    return RunTrace(
        kind=kind,
        run_id=run_id,
        name=row.name,
        status=row.status,
        started_at=root.start,
        completed_at=row.completed_at,
        spans=spans,
        tokens=tokens
        if any(span.kind == "model" and span.tokens is not None for span in spans)
        else None,
    ), threads
