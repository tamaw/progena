"""Workflow run lists are bounded: a recent window plus paged per-workflow history."""

from __future__ import annotations

from datetime import timedelta

import src.backend.workflows.runtime as workflows_runtime
import src.backend.workflows.service as workflows_service
from src.backend.storage.documents import InMemoryDocumentStore
from src.progena.utils import utcnow
from src.progena.workflows.constants import WorkflowRunStatus
from src.progena.workflows.models import WorkflowRun


def _run(
    run_id: str,
    workflow_id: str,
    *,
    age: timedelta,
    status: WorkflowRunStatus = "completed",
) -> WorkflowRun:
    stamp = (utcnow() - age).isoformat()
    return WorkflowRun(
        run_id=run_id,
        workflow_id=workflow_id,
        status=status,
        queued_at=stamp,
        updated_at=stamp,
        node_outputs={"n": "x" * 1000},
        trigger_event={"big": "y" * 1000},
    )


def _store(*runs: WorkflowRun) -> InMemoryDocumentStore:
    store = InMemoryDocumentStore()
    for run in runs:
        workflows_service.save_workflow_run(store, run)
    return store


def test_recent_runs_keep_the_window_open_runs_and_each_latest_run() -> None:
    store = _store(
        _run("fresh", "wf-a", age=timedelta(minutes=5)),
        _run("old-a", "wf-a", age=timedelta(days=2)),
        _run("latest-b", "wf-b", age=timedelta(days=3)),
        _run("old-b", "wf-b", age=timedelta(days=4)),
        _run("waiting", "wf-c", age=timedelta(days=9), status="paused"),
        _run("old-c", "wf-c", age=timedelta(days=10)),
    )

    runs = workflows_service.list_recent_workflow_runs(store)

    assert [run.run_id for run in runs] == ["fresh", "latest-b", "waiting"]


def test_history_pages_one_workflow_newest_first_with_its_total() -> None:
    store = _store(
        *(_run(f"a{i}", "wf-a", age=timedelta(hours=2 + i)) for i in range(5)),
        _run("b0", "wf-b", age=timedelta(hours=2)),
    )

    first, total = workflows_service.list_workflow_run_history(store, "wf-a", offset=0, limit=2)
    last, _ = workflows_service.list_workflow_run_history(store, "wf-a", offset=4, limit=2)

    assert total == 5
    assert [run.run_id for run in first] == ["a0", "a1"]
    assert [run.run_id for run in last] == ["a4"]


def test_snapshot_omits_fields_no_list_reads(monkeypatch) -> None:
    store = _store(_run("r", "wf-a", age=timedelta(minutes=1)))
    sent: list[dict] = []
    monkeypatch.setattr(
        workflows_runtime.storage_documents, "get_default_document_store", lambda: store
    )
    monkeypatch.setattr(
        workflows_runtime.events_stream,
        "broadcast_ui_event",
        lambda _type, payload, **_kw: sent.append(payload),
    )

    workflows_runtime.broadcast_workflow_runs_snapshot(client_id="tab")
    workflows_runtime.broadcast_workflow_runs_snapshot(client_id="tab", workflow_id="wf-a", limit=1)

    recent, history = sent
    assert "node_outputs" not in recent["items"][0] and "trigger_event" not in recent["items"][0]
    assert "workflow_id" not in recent
    assert (history["workflow_id"], history["offset"], history["total"]) == ("wf-a", 0, 1)
