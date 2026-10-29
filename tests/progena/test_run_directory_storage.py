"""Regression coverage for compact /runs/ storage reads."""

from __future__ import annotations

import pytest
from langgraph.store.memory import InMemoryStore

from src.progena.persistence.langgraph import _open_store
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.schedules.models import ScheduleRunRecord
from src.progena.state.manager import StateManager
from src.progena.workflows.documents import workflow_run_key


@pytest.fixture
async def native_state_manager(postgres_url: str):
    """Provide a real AsyncPostgresStore with its schema initialized."""
    native_store, connection = await _open_store(postgres_url)
    manager = StateManager(LangGraphRuntimeStore(native_store), database_url=postgres_url)
    await manager.initialize()
    try:
        yield manager
    finally:
        await manager.close()
        await connection.close()


async def _save_unrelated_documents(store: LangGraphRuntimeStore, count: int = 501) -> None:
    for index in range(count):
        await store.save_document(f"unrelated:{index}", {"thread_id": f"unrelated:{index}"})


@pytest.mark.asyncio
async def test_memory_compact_run_reads_page_past_unrelated_documents() -> None:
    runtime = LangGraphRuntimeStore(InMemoryStore())
    manager = StateManager(runtime)
    await _save_unrelated_documents(runtime)
    await runtime.save_document(
        workflow_run_key("run_123"),
        {
            "run_id": "run_123",
            "workflow_id": "workflow_123",
            "status": "completed",
            "started_at": "2026-10-30T10:00:00+00:00",
            "queued_at": "2026-10-30T09:00:00+00:00",
            "updated_at": "2026-10-30T11:00:00+00:00",
            "node_outputs": {"large": "x" * 100_000},
            "step_audit": [{"large": "x" * 100_000}],
        },
    )

    summaries = await manager.list_workflow_run_summaries()
    assert [row["run_id"] for row in summaries] == ["run_123"]
    assert all("node_outputs" not in row and "step_audit" not in row for row in summaries)


@pytest.mark.asyncio
async def test_native_run_reads_filter_and_project_postgres_payloads(
    native_state_manager: StateManager,
) -> None:
    manager = native_state_manager
    runtime = manager._runtime_store
    await _save_unrelated_documents(runtime)
    await runtime.save_document(
        workflow_run_key("run_123"),
        {
            "run_id": "run_123",
            "workflow_id": "workflow_123",
            "status": "completed",
            "started_at": "2026-10-30T10:00:00+00:00",
            "queued_at": "2026-10-30T09:00:00+00:00",
            "updated_at": "2026-10-30T11:00:00+00:00",
            "node_outputs": {"large": "x" * 100_000},
            "step_audit": [{"large": "x" * 100_000}],
        },
    )
    await manager.save_routine_run_event(
        {
            "request_id": "req_x",
            "status": "started",
            "timestamp": "2026-10-30T10:00:00+00:00",
            "thread_id": "routine:root",
            "output": "x" * 100_000,
        }
    )
    await manager.save_routine_run_event(
        {
            "request_id": "req_x",
            "status": "completed",
            "timestamp": "2026-10-30T10:01:00+00:00",
            "thread_id": "routine:child",
            "output": "x" * 100_000,
        }
    )
    await runtime.save_document(
        "routineXrunXevent:reqAx:started",
        {
            "request_id": "req_x",
            "status": "started",
            "thread_id": "routine:incorrect-prefix",
        },
    )
    await manager.save_routine_graph(
        "routine-key",
        {"id": "wrong", "name": "Routine from key", "nodes": ["large"] * 1000},
    )
    await manager.save_workflow_definition(
        "workflow-key",
        {"id": "wrong", "title": "Workflow from key", "graph": {"nodes": {}}},
    )
    selected = ScheduleRunRecord(
        run_id="schedrun_selected",
        schedule_id="schedule_1",
        thread_id="schedule_1_thread",
        message="selected",
    )
    await runtime.save_document(
        "schedule_runs",
        [
            {
                "run_id": f"schedrun_{index}",
                "schedule_id": "schedule_1",
                "thread_id": f"thread_{index}",
                "message": "unrelated",
            }
            for index in range(501)
        ]
        + [selected.model_dump(mode="json")],
    )

    run = await manager.get_workflow_run("run_123")
    assert run is not None and run["node_outputs"]["large"] == "x" * 100_000
    summaries = await manager.list_workflow_run_summaries()
    assert [row["run_id"] for row in summaries] == ["run_123"]
    assert all("node_outputs" not in row and "step_audit" not in row for row in summaries)
    routine_summaries = await manager.list_routine_run_summaries()
    assert {(row["request_id"], row["status"]) for row in routine_summaries} == {
        ("req_x", "started"),
        ("req_x", "completed"),
    }
    assert all("output" not in row for row in routine_summaries)
    assert [event["thread_id"] for event in await manager.list_routine_run_events("req_x")] == [
        "routine:child",
        "routine:root",
    ]
    assert await manager.list_routine_run_events(thread_ids=["routine:child"]) == [
        {
            "request_id": "req_x",
            "status": "completed",
            "timestamp": "2026-10-30T10:01:00+00:00",
            "thread_id": "routine:child",
            "output": "x" * 100_000,
        }
    ]
    assert await manager.list_routine_titles() == {"routine-key": "Routine from key"}
    assert await manager.list_workflow_titles() == {"workflow-key": "Workflow from key"}
    assert await manager.get_schedule_run("schedrun_selected") == selected
