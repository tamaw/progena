"""Run paging and transcript timing invariants."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.backend.runs.service import _conversation_spans, list_runs
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    InMemoryDocumentStore,
    PostgresDocumentStore,
)


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
async def test_filters_and_keyset_reach_older_runs_beyond_first_source_batch(run_store):
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
    page, cursor = await list_runs(store, kind="workflow", status="failed", q="REPORT", limit=2)
    assert [row.run_id for row in page] == ["run-220", "run-221"]
    assert [(row.steps_done, row.steps_total) for row in page] == [(1, 1), (1, 1)]
    second, cursor = await list_runs(
        store, kind="workflow", status="failed", q="report", limit=2, cursor=cursor
    )
    assert [row.run_id for row in second] == ["run-222", "run-223"]
    last, cursor = await list_runs(
        store, kind="workflow", status="failed", q="report", limit=2, cursor=cursor
    )
    assert [row.run_id for row in last] == ["run-224"]
    assert cursor is None


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
            "tool_id": tool_id,
            "tool_name": "delete",
            "timestamp": timestamp,
            "duration_ms": duration,
            "success": True,
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
