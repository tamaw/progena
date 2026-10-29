"""Run-query normalization parity across persisted and in-memory sources."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.backend.runs.service import list_runs
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    InMemoryDocumentStore,
    PostgresDocumentStore,
    store_database_url,
)
from src.progena.routines.events import RUN_EVENT_PREFIX


@pytest.fixture(params=["memory", "postgres"])
def run_store(request, postgres_url: str):
    if request.param == "memory":
        yield InMemoryDocumentStore()
        return
    store = PostgresDocumentStore(postgres_url)
    store.open()
    try:
        yield store
    finally:
        store.close()


def _save_workflow_run(store, run_id: str, workflow_id: str, started_at: str) -> None:
    store.save_document(
        PROGENA_STATE_PREFIX,
        f"workflow_run:{run_id}",
        {
            "run_id": run_id,
            "workflow_id": workflow_id,
            "source": "manual",
            "status": "completed",
            "started_at": started_at,
            "completed_at": started_at,
        },
    )


def test_workflow_search_filters_before_limit_and_treats_wildcards_literally(run_store):
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:matching",
        {"title": "Straße 100% complete", "graph": {"nodes": {}}},
    )
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:nonmatching",
        {"title": "Straße 1000 complete", "graph": {"nodes": {}}},
    )
    _save_workflow_run(run_store, "matching", "matching", "2026-10-29T10:00:00+00:00")
    _save_workflow_run(run_store, "nonmatching", "nonmatching", "2026-10-29T11:00:00+00:00")

    matching = run_store.query_workflow_run_page(
        status="completed",
        q="straße 100%",
        sort="newest",
        cursor=None,
        since=None,
        as_of="2026-10-29T12:00:00+00:00",
        limit=1,
    )
    folded = run_store.query_workflow_run_page(
        status="completed",
        q="strasse 100%",
        sort="newest",
        cursor=None,
        since=None,
        as_of="2026-10-29T12:00:00+00:00",
        limit=1,
    )

    assert [payload["run_id"] for payload, _ in matching] == ["matching"]
    assert folded == []


@pytest.mark.asyncio
async def test_run_search_uses_lower_semantics_for_workflow_schedule_and_routine(run_store):
    if isinstance(run_store, PostgresDocumentStore):
        database_url = store_database_url(run_store)
        assert database_url is not None
        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()

    started_at = datetime(2026, 10, 29, 10, tzinfo=UTC).isoformat()
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        "workflow:workflow-match",
        {"title": "Straße 100% workflow", "graph": {"nodes": {}}},
    )
    _save_workflow_run(run_store, "workflow-match", "workflow-match", started_at)
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule",
        {"schedule-match": {"name": "Straße 100% schedule"}},
    )
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        "schedule_runs",
        [
            {
                "run_id": "schedule-match",
                "schedule_id": "schedule-match",
                "status": "completed",
                "started_at": started_at,
                "completed_at": started_at,
            }
        ],
    )
    event = {
        "request_id": "routine-match",
        "thread_id": "routine:search:revision:routine-match",
        "name": "Straße 100% routine",
    }
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        f"{RUN_EVENT_PREFIX}routine-match:started",
        {**event, "status": "started", "timestamp": started_at},
    )
    run_store.save_document(
        PROGENA_STATE_PREFIX,
        f"{RUN_EVENT_PREFIX}routine-match:completed",
        {**event, "status": "completed", "timestamp": "2026-10-29T10:01:00+00:00"},
    )

    rows, _ = await list_runs(run_store, q="STRAẞE 100%", status="COMPLETED")
    folded, _ = await list_runs(run_store, q="STRASSE 100%", status="COMPLETED")

    assert {row.run_id for row in rows} == {"workflow-match", "schedule-match", "routine-match"}
    assert folded == []
