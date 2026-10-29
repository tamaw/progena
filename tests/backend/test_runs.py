"""Run paging and transcript timing invariants."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.backend.runs.models import RunRow, RunSort, Span
from src.backend.runs.routes import router as runs_router
from src.backend.runs.service import (
    _conversation_spans,
    _duration_us,
    _rollup_tokens,
    _routine_metadata_rows,
    _routine_overview,
    _thread_spans,
    _workflow_row,
    build_trace,
    checkpoint_row_changed,
    list_runs,
)
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    InMemoryDocumentStore,
    PostgresDocumentStore,
    store_database_url,
)
from src.progena.routines.events import RUN_EVENT_PREFIX


@pytest.fixture(params=["memory", "postgres"])
def run_store(request):
    if request.param == "memory":
        yield InMemoryDocumentStore()
        return
    store = PostgresDocumentStore(request.getfixturevalue("postgres_url"))
    store.open()
    try:
        yield store
    finally:
        store.close()


@pytest.mark.asyncio
async def test_filters_and_keyset_reach_older_runs_beyond_first_source_batch(
    run_store, monkeypatch
):
    store = run_store
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:daily",
        {
            "title": "Daily report",
            "graph": {
                "nodes": {
                    "trigger": {"kind": "manual"},
                    "write": {"kind": "format"},
                    "constant": {"kind": "data"},
                }
            },
        },
    )
    monkeypatch.setattr("src.backend.runs.service._now", lambda: datetime(2026, 11, 1, tzinfo=UTC))
    start = datetime(2026, 10, 28, tzinfo=UTC)
    # Key order and insertion order deliberately disagree with chronological order.
    for index in range(225):
        at = (start - timedelta(minutes=index)).isoformat()
        run_id = f"run-{index:03d}"
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"workflow_run:{run_id}",
            {
                "run_id": run_id,
                "workflow_id": "daily",
                "source": "manual",
                "status": "failed" if index >= 220 else "completed",
                "queued_at": at,
                "started_at": at,
                "completed_at": at,
                "completed_node_ids": ["write", "constant"],
            },
        )
    page, cursor = await list_runs(
        store,
        kind="workflow",
        status="failed",
        q="REPORT",
        period="month",
        limit=2,
    )
    assert [row.run_id for row in page] == ["run-220", "run-221"]
    second, cursor = await list_runs(
        store,
        kind="workflow",
        status="failed",
        q="report",
        period="month",
        limit=2,
        cursor=cursor,
    )
    assert [row.run_id for row in second] == ["run-222", "run-223"]
    last, cursor = await list_runs(
        store,
        kind="workflow",
        status="failed",
        q="report",
        period="month",
        limit=2,
        cursor=cursor,
    )
    assert [row.run_id for row in last] == ["run-224"]
    assert cursor is None


@pytest.mark.asyncio
async def test_global_run_sorting_pages_filtered_cross_kind_results_without_overlap(
    run_store, monkeypatch
):
    """Every sort merges all kinds before its query-bound keyset page is cut."""
    store = run_store
    if isinstance(store, PostgresDocumentStore):
        database_url = store_database_url(store)
        assert database_url is not None
        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()
    monkeypatch.setattr(
        "src.backend.runs.service._now", lambda: datetime(2026, 10, 29, 12, tzinfo=UTC)
    )
    for workflow_id, title in (("matched", "Matched workflow"), ("excluded", "Other workflow")):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"workflow:{workflow_id}",
            {"title": title, "graph": {"nodes": {}}},
        )
    for run_id, workflow_id, started_at, completed_at in (
        ("workflow-tie", "matched", "2026-10-29T11:00:00+00:00", "2026-10-29T11:11:00+00:00"),
        ("workflow-short", "matched", "2026-10-29T10:00:00+00:00", "2026-10-29T10:05:00+00:00"),
        ("workflow-excluded", "excluded", "2026-10-29T13:00:00+00:00", "2026-10-29T13:01:00+00:00"),
    ):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"workflow_run:{run_id}",
            {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "source": "manual",
                "status": "completed",
                "started_at": started_at,
                "completed_at": completed_at,
            },
        )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule",
        {
            "tied": {"name": "Matched schedule"},
            "middle": {"name": "Matched schedule"},
        },
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule_runs",
        [
            {
                "run_id": "schedule-tie",
                "schedule_id": "tied",
                "status": "completed",
                "started_at": "2026-10-29T11:00:00+00:00",
                "completed_at": "2026-10-29T11:11:00+00:00",
            },
            {
                "run_id": "schedule-middle",
                "schedule_id": "middle",
                "status": "completed",
                "started_at": "2026-10-29T09:00:00+00:00",
                "completed_at": "2026-10-29T09:20:00+00:00",
            },
        ],
    )
    for run_id, started_at, completed_at in (
        ("routine-tie", "2026-10-29T11:00:00+00:00", "2026-10-29T11:11:00+00:00"),
        ("routine-long", "2026-10-29T08:00:00+00:00", "2026-10-29T08:30:00+00:00"),
    ):
        payload = {
            "request_id": run_id,
            "thread_id": f"routine:matched:revision:{run_id}",
            "name": "Matched routine",
        }
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{run_id}:started",
            {**payload, "status": "started", "timestamp": started_at},
        )
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{run_id}:completed",
            {**payload, "status": "completed", "timestamp": completed_at},
        )

    async def paged_ids(sort: RunSort) -> list[str]:
        cursor = None
        result: list[str] = []
        while True:
            page, cursor = await list_runs(
                store,
                status="completed",
                q="MATCHED",
                sort=sort,
                cursor=cursor,
                limit=2,
            )
            page_ids = [row.run_id for row in page]
            assert not set(result).intersection(page_ids)
            result.extend(page_ids)
            if cursor is None:
                return result

    assert await paged_ids("newest") == [
        "workflow-tie",
        "schedule-tie",
        "routine-tie",
        "workflow-short",
        "schedule-middle",
        "routine-long",
    ]
    assert await paged_ids("oldest") == [
        "routine-long",
        "schedule-middle",
        "workflow-short",
        "routine-tie",
        "schedule-tie",
        "workflow-tie",
    ]
    assert await paged_ids("duration") == [
        "routine-long",
        "schedule-middle",
        "workflow-tie",
        "schedule-tie",
        "routine-tie",
        "workflow-short",
    ]

    assert await paged_ids("shortest") == [
        "workflow-short",
        "routine-tie",
        "schedule-tie",
        "workflow-tie",
        "schedule-middle",
        "routine-long",
    ]

    _, cursor = await list_runs(store, status="completed", q="matched", sort="newest", limit=2)
    assert cursor is not None
    with pytest.raises(HTTPException, match="does not match query"):
        await list_runs(store, status="completed", q="matched", sort="oldest", cursor=cursor)
    with pytest.raises(HTTPException, match="does not match query"):
        await list_runs(store, status="failed", q="matched", sort="newest", cursor=cursor)
    with pytest.raises(HTTPException, match="does not match query"):
        await list_runs(store, status="completed", q="matched", sort="shortest", cursor=cursor)


@pytest.mark.asyncio
async def test_period_includes_cutoff_across_kinds_normalizes_offsets_and_excludes_missing_starts(
    run_store, monkeypatch
):
    store = run_store
    if isinstance(store, PostgresDocumentStore):
        database_url = store_database_url(store)
        assert database_url is not None
        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()
    monkeypatch.setattr(
        "src.backend.runs.service._now", lambda: datetime(2026, 10, 29, 12, tzinfo=UTC)
    )
    cutoff = "2026-10-28T12:00:00+00:00"
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:daily",
        {"title": "Daily", "graph": {"nodes": {}}},
    )
    for run_id, started_at in (
        ("workflow-at-cutoff", "2026-10-28T08:00:00-04:00"),
        ("workflow-before-cutoff", "2026-10-28T11:59:59+00:00"),
    ):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"workflow_run:{run_id}",
            {
                "run_id": run_id,
                "workflow_id": "daily",
                "status": "completed",
                "source": "manual",
                "queued_at": started_at,
                "started_at": started_at,
                "completed_at": started_at,
            },
        )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:workflow-without-start",
        {"run_id": "workflow-without-start", "workflow_id": "daily", "status": "completed"},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule_runs",
        [
            {
                "run_id": "schedule-at-cutoff",
                "schedule_id": "daily",
                "status": "completed",
                "started_at": cutoff,
                "completed_at": cutoff,
            },
            {
                "run_id": "schedule-before-cutoff",
                "schedule_id": "daily",
                "status": "completed",
                "started_at": "2026-10-28T11:59:59+00:00",
            },
            {"run_id": "schedule-without-start", "schedule_id": "daily", "status": "completed"},
        ],
    )
    for request_id, timestamp in (
        ("routine-at-cutoff", cutoff),
        ("routine-before-cutoff", "2026-10-28T11:59:59+00:00"),
    ):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{request_id}:completed",
            {
                "request_id": request_id,
                "thread_id": f"routine:daily:revision:{request_id}",
                "status": "completed",
                "timestamp": timestamp,
            },
        )

    rows, _ = await list_runs(store, period="day")

    assert {row.run_id for row in rows} == {
        "workflow-at-cutoff",
        "schedule-at-cutoff",
        "routine-at-cutoff",
    }
    assert {row.started_at for row in rows} == {"2026-10-28T12:00:00.000000+00:00"}


def test_runs_route_rejects_invalid_period_or_sort():
    app = FastAPI()
    app.include_router(runs_router, prefix="/api/ui")

    client = TestClient(app)

    assert client.get("/api/ui/runs?period=year").status_code == 422
    assert client.get("/api/ui/runs?sort=fastest").status_code == 422


def test_enqueued_routine_row_is_normalized_to_queued():
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    rows = _routine_metadata_rows(
        InMemoryDocumentStore(),
        [
            {
                "request_id": "attempt",
                "thread_id": "routine:daily:revision:attempt",
                "status": "enqueued",
                "timestamp": at,
            }
        ],
        {},
    )

    assert [(row.run_id, row.status) for row in rows] == [("attempt", "queued")]


@pytest.mark.asyncio
async def test_interrupted_lifecycle_rows_keep_completion_and_duration_unavailable(
    run_store, monkeypatch
):
    store = run_store
    if isinstance(store, PostgresDocumentStore):
        database_url = store_database_url(store)
        assert database_url is not None
        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()
    started = datetime(2026, 10, 28, 10, tzinfo=UTC)
    terminal_at = started + timedelta(minutes=2)
    as_of = started + timedelta(hours=1)
    monkeypatch.setattr("src.backend.runs.service._now", lambda: as_of)
    store.save_document(
        PROGENA_STATE_PREFIX,
        "health",
        {
            "runtime_instance": "replacement-runtime",
            "started_at": (started + timedelta(minutes=30)).isoformat(),
        },
    )

    def lifecycle(request_id, status, timestamp, **fields):
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{request_id}:{status}",
            {
                "request_id": request_id,
                "thread_id": f"routine:daily:revision:{request_id}",
                "status": status,
                "timestamp": timestamp.isoformat(),
                **fields,
            },
        )

    lifecycle("interrupted-routine", "started", started, runtime_instance="gone-runtime")
    lifecycle("terminal-routine", "started", started, runtime_instance="gone-runtime")
    lifecycle("terminal-routine", "stopped", terminal_at, runtime_instance="gone-runtime")
    lifecycle("live-routine", "started", started, runtime_instance="replacement-runtime")
    store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule_runs",
        [
            {
                "run_id": "interrupted-schedule",
                "schedule_id": "daily",
                "status": "running",
                "started_at": started.isoformat(),
                "runtime_instance": "gone-runtime",
            }
        ],
    )

    rows, _ = await list_runs(store, sort="duration", limit=10)
    by_id = {row.run_id: row for row in rows}

    assert by_id["interrupted-routine"].status == "interrupted"
    assert by_id["interrupted-schedule"].status == "interrupted"
    assert by_id["interrupted-routine"].completed_at is None
    assert by_id["interrupted-schedule"].completed_at is None
    assert _duration_us(by_id["interrupted-routine"], as_of.isoformat()) is None
    assert _duration_us(by_id["interrupted-schedule"], as_of.isoformat()) is None
    assert by_id["live-routine"].status == "running"
    assert _duration_us(by_id["live-routine"], as_of.isoformat()) == 3_600_000_000
    assert by_id["terminal-routine"].completed_at == terminal_at.isoformat(timespec="microseconds")

    duration_rows, _ = await list_runs(store, sort="duration", limit=10)
    shortest_rows, _ = await list_runs(store, sort="shortest", limit=10)
    assert [row.run_id for row in duration_rows][-2:] == [
        "interrupted-schedule",
        "interrupted-routine",
    ]
    assert [row.run_id for row in shortest_rows][-2:] == [
        "interrupted-routine",
        "interrupted-schedule",
    ]


def test_missing_audit_preserves_recorded_error_without_inventing_latency():
    store = InMemoryDocumentStore()
    at = datetime(2026, 10, 28, tzinfo=UTC)
    thread = SimpleNamespace(
        messages=[
            AIMessage(
                id="ai",
                content="",
                tool_calls=[
                    {"id": "call-1", "name": "read_file", "args": {"file_path": "/missing"}}
                ],
            ),
            ToolMessage(id="tool", tool_call_id="call-1", content="File not found", status="error"),
        ],
        committed_at={"ai": at, "tool": at + timedelta(seconds=2)},
    )
    spans, tokens = _conversation_spans(store, "parent", thread, set())
    assert tokens == 0
    assert next(span for span in spans if span.kind == "model").tokens is None
    spans = [span for span in spans if span.kind == "tool"]
    assert "/missing" in spans[0].summary
    assert "File not found" in spans[0].summary
    assert [(s.name, s.status, s.error) for s in spans] == [
        ("read_file", "failed", "File not found")
    ]
    assert spans[0].start == spans[0].end == at.isoformat(timespec="microseconds")
    assert spans[0].timing_source == "unavailable"


def test_tool_returned_error_overrides_successful_handler_audit_and_retains_measured_duration():
    store = InMemoryDocumentStore()
    tool_id = "call_ee7600cfc22b4a6e8f217fa4c88095f4"
    timestamp = 1793171725.3236754
    duration = 941.78
    store.save_document(
        "progena.logs.tool_calls",
        "audit",
        {
            "payload": {
                "tool_id": tool_id,
                "tool_name": "delete",
                "timestamp": timestamp,
                "duration_ms": duration,
                "success": True,
            }
        },
    )
    at = datetime.fromtimestamp(timestamp, UTC)
    thread = SimpleNamespace(
        messages=[
            AIMessage(
                id="ai",
                content="",
                tool_calls=[
                    {
                        "id": tool_id,
                        "name": "delete",
                        "args": {"file_path": "/routines/routine_d13d5b6525eb.json"},
                    }
                ],
            ),
            ToolMessage(
                id="tool", tool_call_id=tool_id, content="Permission denied", status="error"
            ),
        ],
        committed_at={"ai": at - timedelta(seconds=1), "tool": at},
    )
    spans, _ = _conversation_spans(store, "parent", thread, set())
    spans = [span for span in spans if span.kind == "tool"]
    assert spans[0].status == "failed"
    assert spans[0].error == "Permission denied"
    assert spans[0].timing_source == "tool-audit-derived"
    assert datetime.fromisoformat(spans[0].end).timestamp() == pytest.approx(timestamp)
    measured = datetime.fromisoformat(spans[0].end) - datetime.fromisoformat(spans[0].start)
    assert measured.total_seconds() * 1000 == pytest.approx(duration, abs=0.001)


def test_shared_thread_messages_belong_to_their_step_window_and_usage_is_deduplicated():
    at = datetime(2026, 10, 28, tzinfo=UTC)
    messages = [
        AIMessage(
            id=f"ai-{index}",
            content="",
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": index + 1,
                "total_tokens": 11 + index,
            },
            tool_calls=[
                {
                    "id": f"call-{index}",
                    "name": "read_file",
                    "args": {"file_path": f"/file-{index}"},
                }
            ],
        )
        for index in range(2)
    ]
    thread = SimpleNamespace(
        messages=messages,
        committed_at={"ai-0": at + timedelta(seconds=1), "ai-1": at + timedelta(seconds=11)},
    )
    seen = set()
    store = InMemoryDocumentStore()
    first, first_usage = _conversation_spans(
        store, "first", thread, seen, at.isoformat(), (at + timedelta(seconds=5)).isoformat()
    )
    second, second_usage = _conversation_spans(
        store,
        "second",
        thread,
        seen,
        (at + timedelta(seconds=10)).isoformat(),
        (at + timedelta(seconds=15)).isoformat(),
    )
    assert first_usage == 11
    assert second_usage == 12
    assert [s.id for s in first] == ["first:model:ai-0", "first:tool:call-0"]
    assert [s.id for s in second] == ["second:model:ai-1", "second:tool:call-1"]
    assert datetime.fromisoformat(second[0].start) == at + timedelta(seconds=10)
    _, repeated_usage = _conversation_spans(store, "repeated", thread, seen)
    assert repeated_usage == 0


@pytest.mark.asyncio
async def test_thread_trace_attaches_nested_subagents_to_their_owning_tool_with_native_saver():
    saver = InMemorySaver()
    store = InMemoryDocumentStore()
    thread_id = "workflow-agent-thread"

    async def save_transcript(
        namespace,
        messages,
        *,
        parents,
        nick,
        pending_messages,
    ):
        metadata = {"parents": parents, "lc_agent_name": nick}
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespace}}
        first_checkpoint = empty_checkpoint()
        first_version = f"{namespace or 'root'}:1"
        first_checkpoint["channel_values"] = {"messages": messages}
        first_checkpoint["channel_versions"] = {"messages": first_version}
        first = await saver.aput(
            config,
            first_checkpoint,
            metadata,
            {"messages": first_version},
        )
        for task_id, message in pending_messages:
            await saver.aput_writes(first, [("messages", message)], task_id)
        tip_checkpoint = empty_checkpoint()
        tip_version = f"{namespace or 'root'}:2"
        tip_checkpoint["channel_values"] = {"messages": messages}
        tip_checkpoint["channel_versions"] = {"messages": tip_version}
        await saver.aput(first, tip_checkpoint, metadata, {"messages": tip_version})
        return first

    root_call = "root-task-call"
    grandchild_call = "grandchild-task-call"
    root_messages = [
        AIMessage(
            id="root-model",
            content="delegate",
            usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            tool_calls=[{"id": root_call, "name": "task", "args": {}}],
        ),
        ToolMessage(id="root-result", tool_call_id=root_call, content="done"),
    ]
    root = await save_transcript(
        "",
        root_messages,
        parents={},
        nick="Root",
        pending_messages=[
            ("root-model-task", root_messages[0]),
            ("root-task", root_messages[1]),
        ],
    )
    child_messages = [
        AIMessage(
            id="child-model",
            content="delegate again",
            tool_calls=[{"id": grandchild_call, "name": "task", "args": {}}],
        ),
        ToolMessage(id="child-result", tool_call_id=grandchild_call, content="done"),
    ]
    child = await save_transcript(
        "tools:root-task",
        child_messages,
        parents={"": root["configurable"]["checkpoint_id"]},
        nick="Child",
        pending_messages=[
            ("child-model-task", child_messages[0]),
            ("grandchild-task", child_messages[1]),
        ],
    )
    grandchild_messages = [
        AIMessage(
            id="grandchild-model",
            content="finished",
            usage_metadata={"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
        )
    ]
    await save_transcript(
        "tools:root-task|tools:grandchild-task",
        grandchild_messages,
        parents={"tools:root-task": child["configurable"]["checkpoint_id"]},
        nick="Grandchild",
        pending_messages=[("grandchild-model-task", grandchild_messages[0])],
    )

    spans, tokens = await _thread_spans(saver, store, "workflow:run:step:0", thread_id, set(), {})

    child_id = f"workflow:run:step:0:subagent:{root_call}"
    grandchild_id = f"{child_id}:subagent:{grandchild_call}"
    by_id = {span.id: span for span in spans}
    assert tokens == 111
    assert by_id[child_id].parent_id == f"workflow:run:step:0:tool:{root_call}"
    assert by_id[grandchild_id].parent_id == f"{child_id}:tool:{grandchild_call}"
    assert by_id[f"{grandchild_id}:model:grandchild-model"].tokens == 100
    assert len(by_id) == len(spans)


@pytest.mark.asyncio
async def test_workflow_list_exposes_compact_audit_overview_and_persisted_zero_usage(run_store):
    store = run_store
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:usage",
        {
            "id": "usage",
            "title": "Usage",
            "graph": {
                "config": {"prompt": "must not be listed"},
                "nodes": {
                    "model": {
                        "kind": "agent",
                        "title": "Model",
                        "label": "Model label",
                        "config": {"prompt": "must not be listed"},
                    }
                },
            },
            "config": {"inputs": {"body": "must not be listed"}},
        },
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:usage",
        {
            "run_id": "usage",
            "workflow_id": "usage",
            "source": "manual",
            "status": "completed",
            "queued_at": at,
            "started_at": at,
            "completed_at": at,
            "completed_node_ids": ["model"],
            "tokens": {"total_tokens": 0},
            "trigger_event": {"body": "not selected"},
            "node_outputs": {"model": {"body": "not selected"}},
            "step_audit": [
                {
                    "node_id": "model",
                    "kind": "agent",
                    "started_at": at,
                    "completed_at": at,
                    "status": "completed",
                    "tokens": {"total_tokens": 0},
                    "output": "not selected",
                    "error": "not selected",
                }
            ],
        },
    )

    rows, _ = await list_runs(store, kind="workflow")

    assert rows[0].tokens == 0
    assert [(span.kind, span.tokens, span.summary, span.error) for span in rows[0].overview] == [
        ("run", 0, None, None),
        ("agent", 0, None, None),
    ]
    payload, definition = store.query_workflow_run_page(
        status="",
        q="",
        sort="newest",
        cursor=None,
        since=None,
        as_of=at,
        limit=1,
    )[0]
    assert "node_outputs" not in payload
    assert "trigger_event" not in payload
    assert set(payload["step_audit"][0]) == {
        "node_id",
        "kind",
        "started_at",
        "completed_at",
        "status",
        "tokens",
    }
    assert definition == {
        "id": "usage",
        "title": "Usage",
        "graph": {"nodes": {"model": {"kind": "agent", "title": "Model", "label": "Model label"}}},
    }


def test_recorded_node_usage_rolls_up_without_usage_metadata_and_deduplicates_hierarchy():
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    row = RunRow(
        kind="routine",
        run_id="stored",
        name="Stored usage",
        trigger="manual",
        status="completed",
        started_at=at,
        completed_at=at,
    )
    overview = _routine_overview(
        row,
        {"nodes": [{"id": "model", "kind": "agent", "label": "Model"}]},
        {
            "model": [
                {
                    "started_at": at,
                    "duration_ms": 0,
                    "tokens": {"input_tokens": 4, "output_tokens": 3},
                }
            ]
        },
    )
    assert _rollup_tokens(overview) == 7

    root = Span(
        id="root",
        kind="run",
        name="Root",
        start=at,
        status="completed",
        tokens=99,
    )
    structural = Span(
        id="structural",
        parent_id="root",
        kind="agent",
        name="Structural",
        start=at,
        status="completed",
        tokens=50,
    )
    model = Span(
        id="model",
        parent_id="structural",
        kind="model",
        name="Model",
        start=at,
        status="completed",
        tokens=0,
    )
    assert _rollup_tokens([root, structural, model]) == 0
    assert (
        _rollup_tokens(
            [
                Span(id="missing", kind="run", name="Missing", start=at, status="completed"),
            ]
        )
        is None
    )


@pytest.mark.asyncio
async def test_trace_uses_recorded_step_usage_when_conversation_usage_is_unavailable():
    store = InMemoryDocumentStore()
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:stored",
        {"title": "Stored", "graph": {"nodes": {"format": {"kind": "format"}}}},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:stored",
        {
            "run_id": "stored",
            "workflow_id": "stored",
            "status": "completed",
            "queued_at": at,
            "started_at": at,
            "completed_at": at,
            "tokens": {"total_tokens": 7},
            "step_audit": [
                {
                    "node_id": "format",
                    "kind": "format",
                    "started_at": at,
                    "completed_at": at,
                    "status": "completed",
                    "tokens": {"total_tokens": 7},
                }
            ],
        },
    )

    trace, _ = await build_trace(store, "workflow", "stored")

    assert trace.tokens == 7
    assert next(span for span in trace.spans if span.kind == "step").tokens == 7


@pytest.mark.asyncio
async def test_failed_workflow_step_uses_persisted_audit_thread_for_trace(monkeypatch):
    store = InMemoryDocumentStore()
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:stored",
        {"title": "Stored", "graph": {"nodes": {"agent": {"kind": "agent"}}}},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:stored",
        {
            "run_id": "stored",
            "workflow_id": "stored",
            "status": "failed",
            "queued_at": at,
            "started_at": at,
            "completed_at": at,
            "last_error": "runner failed",
            "step_audit": [
                {
                    "node_id": "agent",
                    "kind": "agent",
                    "thread_id": "wired-thread",
                    "started_at": at,
                    "completed_at": at,
                    "status": "failed",
                    "error": "runner failed",
                }
            ],
        },
    )
    observed = []

    @asynccontextmanager
    async def saver(_store):
        yield object()

    async def thread_spans(_saver, _store, _parent_id, thread_id, *_args):
        observed.append(thread_id)
        return [], 0

    monkeypatch.setattr("src.backend.runs.service._trace_saver", saver)
    monkeypatch.setattr("src.backend.runs.service._thread_spans", thread_spans)

    trace, _ = await build_trace(store, "workflow", "stored")

    assert observed == ["wired-thread"]
    assert trace.spans[0].error == "runner failed"


@pytest.mark.asyncio
async def test_workflow_trace_uses_recorded_routine_kind_after_node_changes(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class CheckpointState(TypedDict):
        run_meta: dict[str, list[dict[str, object]]]

    store = InMemoryDocumentStore()
    started_at = datetime(2026, 10, 28, tzinfo=UTC)
    at = started_at.isoformat()
    completed_at = (started_at + timedelta(minutes=2)).isoformat()
    work_started_at = (started_at + timedelta(seconds=30)).isoformat()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:stored",
        {"title": "Stored", "graph": {"nodes": {"child": {"kind": "routine"}}}},
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:stored",
        {
            "run_id": "stored",
            "workflow_id": "stored",
            "status": "completed",
            "queued_at": at,
            "started_at": at,
            "completed_at": completed_at,
            "node_outputs": {"child": {"thread_id": "routine:child:revision:run"}},
            "step_audit": [
                {
                    "node_id": "child",
                    "kind": "routine",
                    "started_at": at,
                    "completed_at": completed_at,
                    "status": "completed",
                }
            ],
        },
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:stored",
        {"title": "Stored", "graph": {"nodes": {"child": {"kind": "agent"}}}},
    )
    store.upsert_routine_revision(
        "child",
        "revision",
        {
            "name": "Child",
            "nodes": [{"id": "work", "kind": "prompt", "label": "Historical Work"}],
        },
    )
    saver = InMemorySaver()
    builder = StateGraph(CheckpointState)
    builder.add_node(
        "record",
        lambda _state: {
            "run_meta": {
                "work": [
                    {
                        "started_at": work_started_at,
                        "duration_ms": 0,
                        "tokens": {"total_tokens": 17},
                    }
                ]
            }
        },
    )
    builder.add_edge(START, "record")
    builder.add_edge("record", END)
    graph = builder.compile(checkpointer=saver)
    await graph.ainvoke(
        {},
        {"configurable": {"thread_id": "routine:child:revision:run"}},
    )

    @asynccontextmanager
    async def native_memory_saver(_store):
        yield saver

    monkeypatch.setattr("src.backend.runs.service._trace_saver", native_memory_saver)

    trace, threads = await build_trace(store, "workflow", "stored")

    assert "routine:child:revision:run" in threads
    assert trace.tokens == 17
    assert next(span for span in trace.spans if span.id.endswith(":step:0")).kind == "routine"
    historical = next(span for span in trace.spans if span.name == "Historical Work")
    assert historical.kind == "step"
    assert historical.tokens == 17


@pytest.mark.asyncio
async def test_workflow_page_hydrates_referenced_routine_tip_usage_without_history(monkeypatch):
    store = InMemoryDocumentStore()
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:parent",
        {
            "title": "Parent",
            "graph": {"nodes": {"child": {"kind": "routine", "title": "Child routine"}}},
        },
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow_run:parent",
        {
            "run_id": "parent",
            "workflow_id": "parent",
            "status": "completed",
            "completed_node_ids": ["child"],
            "queued_at": at,
            "started_at": at,
            "completed_at": at,
            "node_outputs": {"child": {"thread_id": "routine:child:revision:run"}},
            "step_audit": [
                {
                    "node_id": "child",
                    "kind": "routine",
                    "started_at": at,
                    "completed_at": at,
                    "status": "completed",
                }
            ],
        },
    )

    class Saver:
        async def aget_tuple(self, config):
            assert config["configurable"]["thread_id"] == "routine:child:revision:run"
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "run_meta": {
                            "prompt": [
                                {
                                    "started_at": at,
                                    "duration_ms": 0,
                                    "tokens": {"total_tokens": 13},
                                }
                            ]
                        }
                    }
                }
            )

    @asynccontextmanager
    async def saver(_store):
        yield Saver()

    async def graph(_store, _thread_id, _event=None):
        return {"nodes": [{"id": "prompt", "kind": "prompt", "label": "Prompt"}]}

    async def no_history(*_args, **_kwargs):
        raise AssertionError("workflow page must not load conversation history")

    monkeypatch.setattr("src.backend.runs.service._thread_spans", no_history)

    monkeypatch.setattr("src.backend.runs.service._trace_saver", saver)
    monkeypatch.setattr("src.backend.runs.service._routine_graph", graph)

    rows, _ = await list_runs(store, kind="workflow")

    assert rows[0].tokens == 13
    root = next(span for span in rows[0].overview if span.kind == "run")
    owner = next(span for span in rows[0].overview if span.kind == "routine")
    child = next(span for span in rows[0].overview if span.kind == "step")
    assert root.tokens is None
    assert owner.parent_id == root.id
    assert child.parent_id == owner.id
    assert child.tokens == 13


def test_workflow_pending_routine_reference_is_compactly_retained():
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    row = _workflow_row(
        InMemoryDocumentStore(),
        {
            "run_id": "pending",
            "workflow_id": "workflow",
            "status": "running",
            "queued_at": at,
            "started_at": at,
            "pending_routine": {
                "node_id": "child",
                "thread_id": "routine:child:revision:run",
            },
            "step_audit": [
                {
                    "node_id": "child",
                    "kind": "routine",
                    "started_at": at,
                    "status": "running",
                }
            ],
        },
        {"graph": {"nodes": {"child": {"kind": "routine"}}}},
    )

    assert row._routine_threads == {
        "workflow:pending:step:0": "routine:child:revision:run",
    }


def test_workflow_repeated_routine_audit_hydrates_one_latest_thread_reference():
    at = datetime(2026, 10, 28, tzinfo=UTC).isoformat()
    row = _workflow_row(
        InMemoryDocumentStore(),
        {
            "run_id": "repeated",
            "workflow_id": "workflow",
            "status": "completed",
            "queued_at": at,
            "started_at": at,
            "node_threads": {"child": {"thread_id": "routine:child:revision:run"}},
            "step_audit": [
                {
                    "node_id": "child",
                    "kind": "routine",
                    "started_at": at,
                    "status": "completed",
                },
                {
                    "node_id": "child",
                    "kind": "routine",
                    "started_at": at,
                    "status": "completed",
                },
            ],
        },
        {"graph": {"nodes": {"child": {"kind": "routine"}}}},
    )

    assert row._routine_threads == {
        "workflow:repeated:step:1": "routine:child:revision:run",
    }


def test_checkpoint_window_excludes_boundary_models_and_tool_audits():
    at = datetime(2026, 10, 29, 10, tzinfo=UTC)
    inside = AIMessage(
        id="inside",
        content="inside",
        usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
        tool_calls=[{"id": "inside-call", "name": "read_file", "args": {"path": "/inside"}}],
    )
    next_attempt = AIMessage(
        id="next",
        content="next attempt",
        usage_metadata={"input_tokens": 20, "output_tokens": 9, "total_tokens": 29},
        tool_calls=[{"id": "next-call", "name": "read_file", "args": {"path": "/next"}}],
    )
    thread = SimpleNamespace(
        messages=[inside, next_attempt],
        committed_at={"inside": at + timedelta(minutes=1), "next": at + timedelta(minutes=3)},
    )

    class AuditStore(InMemoryDocumentStore):
        def __init__(self):
            super().__init__()
            self.queried: set[str] = set()

        def query_tool_audits(self, ids):
            self.queried.update(ids)
            return {
                "inside-call": {
                    "tool_name": "read_file",
                    "timestamp": (at + timedelta(minutes=1, seconds=1)).timestamp(),
                    "duration_ms": 100,
                    "success": True,
                }
            }

    store = AuditStore()

    spans, tokens = _conversation_spans(
        store,
        "attempt",
        thread,
        set(),
        at.isoformat(),
        (at + timedelta(minutes=3)).isoformat(),
    )

    assert tokens == 11
    assert store.queried == {"inside-call"}
    assert {span.id for span in spans} == {
        "attempt:model:inside",
        "attempt:tool:inside-call",
    }


@pytest.mark.asyncio
async def test_resumed_attempts_keep_checkpoint_meta_and_row_updates_isolated(monkeypatch):
    store = InMemoryDocumentStore()
    thread_id = "routine:daily:rev:shared"
    child_thread_id = "routine:child:rev:shared"
    started_a = datetime(2026, 10, 29, 10, tzinfo=UTC)
    stopped_a = started_a + timedelta(minutes=2)
    queued_b = stopped_a + timedelta(seconds=30)
    started_b = started_a + timedelta(minutes=3)
    store.upsert_routine_revision(
        "daily",
        "rev",
        {"name": "Daily", "nodes": [{"id": "call", "kind": "routine", "label": "Call"}]},
    )
    store.upsert_routine_revision(
        "child",
        "rev",
        {"name": "Child", "nodes": [{"id": "work", "kind": "prompt", "label": "Work"}]},
    )

    def lifecycle(request_id, status, timestamp, *, target_thread=thread_id, parent=None):
        payload = {
            "request_id": request_id,
            "thread_id": target_thread,
            "agent_id": "daily",
            "revision_id": "rev",
            "status": status,
            "timestamp": timestamp.isoformat(),
        }
        if parent is not None:
            payload["parent"] = parent
        store.save_document(
            PROGENA_STATE_PREFIX,
            f"{RUN_EVENT_PREFIX}{request_id}:{status}",
            payload,
        )

    lifecycle("attempt-a", "enqueued", started_a - timedelta(minutes=1))
    lifecycle("attempt-a", "started", started_a)
    lifecycle("attempt-a", "stopped", stopped_a)
    lifecycle("attempt-b", "enqueued", queued_b)
    lifecycle("attempt-b", "started", started_b)
    child_parent = {"thread_id": thread_id, "node_id": "call"}
    lifecycle(
        "child-a",
        "started",
        started_a + timedelta(minutes=1, seconds=15),
        target_thread=child_thread_id,
        parent=child_parent,
    )
    lifecycle(
        "child-a",
        "stopped",
        stopped_a,
        target_thread=child_thread_id,
        parent=child_parent,
    )
    lifecycle(
        "child-b",
        "started",
        started_b + timedelta(minutes=1, seconds=15),
        target_thread=child_thread_id,
        parent=child_parent,
    )

    class MemorySaver:
        async def aget_tuple(self, config):
            target_thread = config["configurable"]["thread_id"]
            assert target_thread in {thread_id, child_thread_id}
            meta = {
                thread_id: {
                    "call": [
                        {
                            "started_at": (started_a + timedelta(minutes=1)).isoformat(),
                            "duration_ms": 60_000,
                            "routine": {"request_id": "child-a", "thread_id": child_thread_id},
                            "tokens": {"total_tokens": 11},
                        },
                        {
                            "started_at": (started_b + timedelta(minutes=1)).isoformat(),
                            "duration_ms": 60_000,
                            "routine": {"request_id": "child-b", "thread_id": child_thread_id},
                            "tokens": {"total_tokens": 29},
                        },
                    ]
                },
                child_thread_id: {
                    "work": [
                        {
                            "started_at": (
                                started_a + timedelta(minutes=1, seconds=30)
                            ).isoformat(),
                            "duration_ms": 0,
                            "tokens": {"total_tokens": 11},
                        },
                        {
                            "started_at": (
                                started_b + timedelta(minutes=1, seconds=30)
                            ).isoformat(),
                            "duration_ms": 0,
                            "tokens": {"total_tokens": 29},
                        },
                    ]
                },
            }[target_thread]
            return SimpleNamespace(checkpoint={"channel_values": {"run_meta": meta}})

    @asynccontextmanager
    async def native_memory_saver(_store):
        yield MemorySaver()

    monkeypatch.setattr("src.backend.runs.service._trace_saver", native_memory_saver)

    first, _ = await build_trace(store, "routine", "attempt-a")
    resumed, _ = await build_trace(store, "routine", "attempt-b")
    rows = await checkpoint_row_changed(store, thread_id)

    assert first.tokens == 11
    assert resumed.tokens == 29
    assert [span.tokens for span in first.spans if span.id.endswith(":node:work:0")] == [11]
    assert [span.tokens for span in resumed.spans if span.id.endswith(":node:work:1")] == [29]
    assert not any(span.tokens == 29 for span in first.spans)
    queue = next(span for span in resumed.spans if span.kind == "queue")
    assert datetime.fromisoformat(queue.start) == queued_b
    assert datetime.fromisoformat(queue.end) == started_b
    assert {row.run_id: row.tokens for row in rows} == {"attempt-a": 11, "attempt-b": 29}
